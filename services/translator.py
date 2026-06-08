"""
按章翻译服务 (deepseek-v4-flash, 非思考模式)。

设计要点:
  - 粒度: 一章 = 一次 API 调用, 段数对齐靠空行切分
  - 上下文: 上一章英译全文 (超 30K 字符则取头尾各半)
  - 模型: deepseek-v4-flash 非思考模式 (extra_body.thinking.type=disabled)
  - 超长拒绝: 单章字符 > CHAPTER_MAX_CHARS 直接拒绝, 标记 paragraph.status=3
  - 客户端: openai.AsyncOpenAI (官方 SDK, OpenAI 兼容格式)
"""
from __future__ import annotations

import os
import re
from typing import Optional

from dotenv import load_dotenv
from openai import APIError, AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy.orm import selectinload

from models import Book, Chapter, Paragraph

load_dotenv()

DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_API_BASE: str = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
CHAPTER_MAX_CHARS: int = int(os.getenv("CHAPTER_MAX_CHARS", "20000"))
PREV_CHAPTER_MAX_CHARS: int = 30000  # 上一章英译塞入 prompt 的字符上限

# Paragraph.status 含义 (在 models.Paragraph 同一定义):
#   0 = pending, 1 = in_progress, 2 = done, -1 = failed, 3 = too_long
STATUS_TOO_LONG = 3


SYSTEM_PROMPT = """你是一位资深中英双语文学翻译。
- 将用户给定的整章中文翻译为英文, 保留原文语气、风格、文学性。
- 保持与原文相同的段落数, 段与段之间用一个空行分隔。
- 只输出 N 段英文译文, 段间空行分隔, 严禁任何解释、标题、注释或额外内容。"""


def _build_user_prompt(
    chapter_title: str,
    chapter_zh: str,
    prev_chapter_english: Optional[str],
) -> str:
    parts: list[str] = []
    if prev_chapter_english:
        parts.append("上一章英译 (供术语 / 风格衔接参考):")
        parts.append(prev_chapter_english)
        parts.append("\n---\n")
    parts.append(f"Chapter: {chapter_title}\n请翻译以下段落:")
    parts.append(chapter_zh)
    return "\n".join(parts)


def _truncate_middle(text: str, limit: int) -> str:
    """字符超限时, 取头尾各一半, 中间用占位符连接。"""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n\n[... middle truncated ...]\n\n{text[-half:]}"


def _parse_translated_paragraphs(response_text: str, expected_n: int) -> list[str]:
    """按空行切分 LLM 响应, 段数对齐到 expected_n (不足补空, 多余截断)。"""
    chunks = re.split(r"\n\s*\n", response_text or "")
    chunks = [c.strip() for c in chunks if c.strip()]
    if len(chunks) != expected_n:
        print(
            f"[translator] chapter paragraph count mismatch: "
            f"expected {expected_n}, got {len(chunks)} -> 补/截到 {expected_n}"
        )
        if len(chunks) < expected_n:
            chunks.extend([""] * (expected_n - len(chunks)))
        else:
            chunks = chunks[:expected_n]
    return chunks


async def _load_prev_chapter_english(
    session: AsyncSession,
    book_id: int,
    chapter_index: int,
) -> Optional[str]:
    """加载上一章所有段落的英译, 拼成单字符串, 过长截断。"""
    if chapter_index <= 0:
        return None
    stmt = (
        select(Chapter)
        .where(Chapter.book_id == book_id, Chapter.chapter_index == chapter_index - 1)
        .options(selectinload(Chapter.paragraphs))
    )
    prev = (await session.execute(stmt)).scalar_one_or_none()
    if prev is None:
        return None
    paragraphs = sorted(prev.paragraphs, key=lambda p: p.paragraph_index)
    text = "\n\n".join(p.translated_text for p in paragraphs if p.translated_text)
    if not text:
        return None
    return _truncate_middle(text, PREV_CHAPTER_MAX_CHARS)


async def translate_chapter(
    chapter_id: int,
    session_maker: async_sessionmaker[AsyncSession],
) -> dict:
    """
    翻译指定章节。返回:
      {"status": "done"}
      {"status": "too_long", "char_count": N}
      {"status": "failed", "reason": "..."}
      {"status": "skipped", "reason": "DEEPSEEK_API_KEY not configured"}
    """
    # 1) 加载章节
    async with session_maker() as s:
        chapter = await s.get(Chapter, chapter_id, options=[selectinload(Chapter.paragraphs)])
        if chapter is None:
            return {"status": "failed", "reason": "chapter not found"}
        paragraphs = sorted(chapter.paragraphs, key=lambda p: p.paragraph_index)
        if not paragraphs:
            return {"status": "skipped", "reason": "empty chapter"}
        book_id = chapter.book_id
        chapter_index = chapter.chapter_index
        chapter_title = chapter.title

    # 2) 超长拒绝 (无兜底)
    char_count = sum(len(p.original_text or "") for p in paragraphs)
    if char_count > CHAPTER_MAX_CHARS:
        print(
            f"[translator] chapter {chapter_id} 过长: {char_count} chars > "
            f"{CHAPTER_MAX_CHARS}, 拒绝翻译"
        )
        async with session_maker() as s:
            for p in paragraphs:
                p.status = STATUS_TOO_LONG
            await s.commit()
        return {"status": "too_long", "char_count": char_count}

    if not DEEPSEEK_API_KEY:
        return {"status": "skipped", "reason": "DEEPSEEK_API_KEY 未配置"}

    # 3) 标记为翻译中
    async with session_maker() as s:
        for p in paragraphs:
            p.status = 1
        await s.commit()

    # 4) 加载上一章英译 (上下文)
    async with session_maker() as s:
        prev_english = await _load_prev_chapter_english(s, book_id, chapter_index)

    # 5) 构造 prompt
    chapter_zh = "\n\n".join(p.original_text for p in paragraphs)
    user_prompt = _build_user_prompt(
        chapter_title=chapter_title,
        chapter_zh=chapter_zh,
        prev_chapter_english=prev_english,
    )

    # 6) 调用 DeepSeek (OpenAI 兼容格式, 非思考模式)
    try:
        client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_API_BASE,
            timeout=120.0,
        )
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8000,
            extra_body={"thinking": {"type": "disabled"}},
        )
        translated_text = (response.choices[0].message.content or "").strip()
    except APIError as exc:
        print(f"[translator] chapter {chapter_id} API error: {exc!r}")
        async with session_maker() as s:
            for p in paragraphs:
                p.status = -1
            await s.commit()
        return {"status": "failed", "reason": f"API error: {exc!r}"}
    except Exception as exc:  # noqa: BLE001
        print(f"[translator] chapter {chapter_id} unexpected error: {exc!r}")
        async with session_maker() as s:
            for p in paragraphs:
                p.status = -1
            await s.commit()
        return {"status": "failed", "reason": f"unexpected: {exc!r}"}

    if not translated_text:
        async with session_maker() as s:
            for p in paragraphs:
                p.status = -1
            await s.commit()
        return {"status": "failed", "reason": "empty response"}

    # 7) 解析响应
    translated_paragraphs = _parse_translated_paragraphs(translated_text, len(paragraphs))

    # 8) 写回 DB
    async with session_maker() as s:
        for i, p in enumerate(paragraphs):
            text = translated_paragraphs[i]
            p.translated_text = text
            p.status = 2 if text else -1
        # 刷新 Book.translated_count: 单条 SQL COUNT 替代 ORM 循环 (10000 段 ~5ms)
        total_done = (
            await s.execute(
                select(func.count(Paragraph.id))
                .where(Paragraph.chapter_id.in_(
                    select(Chapter.id).where(Chapter.book_id == book_id)
                ))
                .where(Paragraph.status == 2)
            )
        ).scalar() or 0
        book = await s.get(Book, book_id)
        if book is not None:
            book.translated_count = total_done
        await s.commit()

    return {"status": "done"}
