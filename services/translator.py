"""DeepSeek 翻译服务: 滑动窗口上下文 + 并发限流。"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from models import Book, Chapter, Paragraph

load_dotenv()

DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_API_BASE: str = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1").rstrip("/")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
MAX_CONCURRENT: int = int(os.getenv("MAX_CONCURRENT_TRANSLATIONS", "3"))


SYSTEM_PROMPT = """你是一位资深中英双语文学翻译。
- 翻译必须自然流畅、地道, 保留原文语气、风格和文学性。
- 用户会提供前两段中文原文作为【语境参考】, 请勿翻译, 也不要重复输出。
- 只输出【当前段落】对应的英文译文, 不要输出任何解释、注释、标题或额外内容。"""


def _build_user_prompt(current_text: str, context_chinese: list[str]) -> str:
    """构造带滑动窗口上下文的 Prompt。"""
    if context_chinese:
        ctx = "\n".join(f"[{i + 1}] {c}" for i, c in enumerate(context_chinese))
        return (
            "以下为前两段中文原文(仅作语境参考, 请勿翻译):\n"
            f"{ctx}\n\n"
            "---\n\n"
            "请翻译当前段落:\n"
            f"{current_text}\n\n"
            "要求: 只输出当前段落的英文译文, 不要重复前文, 不要添加任何解释。"
        )
    return (
        "请将以下中文翻译为英文:\n"
        f"{current_text}\n\n"
        "要求: 只输出英文译文, 不要添加任何解释。"
    )


async def _translate_one(
    client: httpx.AsyncClient,
    text: str,
    context_chinese: list[str],
    semaphore: asyncio.Semaphore,
) -> Optional[str]:
    """单段落翻译, 自动限流。"""
    if not DEEPSEEK_API_KEY:
        return None

    async with semaphore:
        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(text, context_chinese)},
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            resp = await client.post(
                f"{DEEPSEEK_API_BASE}/chat/completions",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
            resp.raise_for_status()
            data = resp.json()
            return (data["choices"][0]["message"]["content"] or "").strip() or None
        except Exception as exc:  # noqa: BLE001
            print(f"[translator] translate error: {exc!r}")
            return None


async def _refresh_progress(session: AsyncSession, book_id: int) -> None:
    """刷新 Book.translated_count。"""
    stmt = (
        select(func.count(Paragraph.id))
        .join(Chapter, Paragraph.chapter_id == Chapter.id)
        .where(Chapter.book_id == book_id, Paragraph.status == 2)
    )
    result = await session.execute(stmt)
    done = result.scalar() or 0

    book = await session.get(Book, book_id)
    if book is not None:
        book.translated_count = done
        await session.commit()


async def _process_one(
    client: httpx.AsyncClient,
    session_maker: async_sessionmaker[AsyncSession],
    book_id: int,
    para_id: int,
    text: str,
    context_chinese: list[str],
    semaphore: asyncio.Semaphore,
) -> None:
    """翻译一段并写回 DB, 然后刷新进度。"""
    # 标记为"翻译中"
    async with session_maker() as s:
        p = await s.get(Paragraph, para_id)
        if p is None:
            return
        p.status = 1
        await s.commit()

    translated = await _translate_one(client, text, context_chinese, semaphore)

    async with session_maker() as s:
        p = await s.get(Paragraph, para_id)
        if p is None:
            return
        if translated:
            p.translated_text = translated
            p.status = 2
        else:
            p.status = -1  # 失败
        await s.commit()

    async with session_maker() as s:
        await _refresh_progress(s, book_id)


async def translate_book_background(
    book_id: int,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """
    后台任务入口: 顺序构建上下文, 并发执行 API 调用 (受 Semaphore 限流)。
    """
    if not DEEPSEEK_API_KEY:
        print(f"[translator] book {book_id}: DEEPSEEK_API_KEY 未配置, 跳过翻译。")
        return

    # 1) 拉取全部段落 (按章节顺序、段内顺序)
    async with session_maker() as s:
        stmt = (
            select(Paragraph)
            .join(Chapter, Paragraph.chapter_id == Chapter.id)
            .where(Chapter.book_id == book_id)
            .order_by(Chapter.chapter_index, Paragraph.paragraph_index)
        )
        rows = (await s.execute(stmt)).scalars().all()

    # 2) 过滤: 只翻译有内容且尚未翻译的
    todo: list[Paragraph] = [
        p for p in rows if (p.original_text or "").strip() and not p.translated_text
    ]
    if not todo:
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient() as client:
        tasks = []
        for i, p in enumerate(todo):
            # 滑动窗口: N-1 与 N-2 段的中文作为 Context
            context: list[str] = []
            if i >= 1:
                context.append(todo[i - 1].original_text)
            if i >= 2:
                context.append(todo[i - 2].original_text)
            tasks.append(
                _process_one(
                    client=client,
                    session_maker=session_maker,
                    book_id=book_id,
                    para_id=p.id,
                    text=p.original_text,
                    context_chinese=context,
                    semaphore=semaphore,
                )
            )
        # 并发执行, 单段失败不影响其他段
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                print(f"[translator] task error: {r!r}")
