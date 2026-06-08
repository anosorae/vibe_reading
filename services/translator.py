"""
按章翻译服务 (OpenAI 兼容格式)。

设计要点:
  - 粒度: 一章 = 一次 API 调用
  - 输出: 中文段落 + 分隔线 + 英文段落 (双语对照)
  - 上下文: 上一章双语译文 (超 30K 字符则取头尾各半)
  - 超长拒绝: 单章字符 > CHAPTER_MAX_CHARS 直接拒绝, 标记 chapter.status=3
  - 客户端: openai.AsyncOpenAI (官方 SDK, OpenAI 兼容格式)
  - 流式输出: translate_chapter_stream() 逐 token 输出, 前端实时显示
"""
from __future__ import annotations

import json
import os
import re
from typing import AsyncIterator, Optional

from dotenv import load_dotenv
from openai import APIError, AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from models import Book, Chapter

load_dotenv()

LLM_API_KEY: str = os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")).strip()
LLM_API_BASE: str = os.getenv("LLM_API_BASE", os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")).rstrip("/")
LLM_MODEL: str = os.getenv("LLM_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))
CHAPTER_MAX_CHARS: int = int(os.getenv("CHAPTER_MAX_CHARS", "20000"))
PREV_CHAPTER_MAX_CHARS: int = 30000

_IS_DEEPSEEK = "deepseek" in LLM_API_BASE.lower()

STATUS_TOO_LONG = 3

SYSTEM_PROMPT = """你是一位资深中英文学翻译。
将用户给定的整章中文翻译为英文, 保留原文语气、风格、文学性。
保持与原文完全相同的段落数, 段与段之间用一个空行分隔。
只输出英文译文, 段间空行分隔, 严禁任何解释、标题、注释或额外内容。"""


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
    parts.append(f"Chapter: {chapter_title}")
    parts.append("请将以下整章中文翻译为英文:")
    parts.append(chapter_zh)
    return "\n".join(parts)


def _truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n\n[... middle truncated ...]\n\n{text[-half:]}"


async def _load_prev_chapter_english(
    session: AsyncSession,
    book_id: int,
    chapter_index: int,
) -> Optional[str]:
    if chapter_index <= 0:
        return None
    stmt = select(Chapter.translated_content).where(
        Chapter.book_id == book_id,
        Chapter.chapter_index == chapter_index - 1,
        Chapter.status == 2,
    )
    result = (await session.execute(stmt)).scalar_one_or_none()
    if not result:
        return None
    return _truncate_middle(result, PREV_CHAPTER_MAX_CHARS)


async def translate_chapter(
    chapter_id: int,
    session_maker: async_sessionmaker[AsyncSession],
) -> dict:
    """
    翻译指定章节。返回:
      {"status": "done"}
      {"status": "too_long", "char_count": N}
      {"status": "failed", "reason": "..."}
      {"status": "skipped", "reason": "..."}
    """
    # 1) 加载章节
    async with session_maker() as s:
        chapter = await s.get(Chapter, chapter_id)
        if chapter is None:
            return {"status": "failed", "reason": "chapter not found"}
        if not chapter.content:
            return {"status": "skipped", "reason": "empty chapter"}
        book_id = chapter.book_id
        chapter_index = chapter.chapter_index
        chapter_title = chapter.title
        chapter_content = chapter.content

    # 2) 超长拒绝
    char_count = len(chapter_content)
    if char_count > CHAPTER_MAX_CHARS:
        print(f"[translator] chapter {chapter_id} 过长: {char_count} chars > {CHAPTER_MAX_CHARS}")
        async with session_maker() as s:
            ch = await s.get(Chapter, chapter_id)
            ch.status = STATUS_TOO_LONG
            await s.commit()
        return {"status": "too_long", "char_count": char_count}

    if not LLM_API_KEY:
        return {"status": "skipped", "reason": "LLM_API_KEY 未配置"}

    # 3) 标记为翻译中
    async with session_maker() as s:
        ch = await s.get(Chapter, chapter_id)
        ch.status = 1
        await s.commit()

    # 4) 加载上一章英译 (上下文)
    async with session_maker() as s:
        prev_english = await _load_prev_chapter_english(s, book_id, chapter_index)

    # 5) 构造 prompt
    user_prompt = _build_user_prompt(
        chapter_title=chapter_title,
        chapter_zh=chapter_content,
        prev_chapter_english=prev_english,
    )

    # 6) 调用 LLM
    try:
        client = AsyncOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_API_BASE,
            timeout=120.0,
        )
        create_kwargs = dict(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=16000,
        )
        if _IS_DEEPSEEK:
            create_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        response = await client.chat.completions.create(**create_kwargs)
        translated_text = (response.choices[0].message.content or "").strip()
    except APIError as exc:
        print(f"[translator] chapter {chapter_id} API error: {exc!r}")
        async with session_maker() as s:
            ch = await s.get(Chapter, chapter_id)
            ch.status = -1
            await s.commit()
        return {"status": "failed", "reason": f"API error: {exc!r}"}
    except Exception as exc:
        print(f"[translator] chapter {chapter_id} unexpected error: {exc!r}")
        async with session_maker() as s:
            ch = await s.get(Chapter, chapter_id)
            ch.status = -1
            await s.commit()
        return {"status": "failed", "reason": f"unexpected: {exc!r}"}

    if not translated_text:
        async with session_maker() as s:
            ch = await s.get(Chapter, chapter_id)
            ch.status = -1
            await s.commit()
        return {"status": "failed", "reason": "empty response"}

    # 7) 写回 DB
    async with session_maker() as s:
        ch = await s.get(Chapter, chapter_id)
        ch.translated_content = translated_text
        ch.status = 2
        total_done = (await s.execute(
            select(func.count(Chapter.id))
            .where(Chapter.book_id == book_id, Chapter.status == 2)
        )).scalar() or 0
        book = await s.get(Book, book_id)
        if book is not None:
            book.translated_chapters = total_done
        await s.commit()

    return {"status": "done"}


async def translate_chapter_stream(
    chapter_id: int,
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[str]:
    """
    流式翻译指定章节。通过 SSE 逐 chunk 输出。

    Yields JSON 字符串, 格式:
      {"type": "status", "status": "too_long", "char_count": N}
      {"type": "status", "status": "started", "char_count": N}
      {"type": "progress", "chars": N}
      {"type": "chunk", "text": "..."}
      {"type": "done", "text": "..."}
      {"type": "error", "reason": "..."}
    """
    def _emit(event: dict) -> str:
        return json.dumps(event, ensure_ascii=False)

    # 1) 加载章节
    async with session_maker() as s:
        chapter = await s.get(Chapter, chapter_id)
        if chapter is None:
            yield _emit({"type": "error", "reason": "chapter not found"})
            return
        if not chapter.content:
            yield _emit({"type": "error", "reason": "empty chapter"})
            return
        book_id = chapter.book_id
        chapter_index = chapter.chapter_index
        chapter_title = chapter.title
        chapter_content = chapter.content

    # 2) 超长拒绝
    char_count = len(chapter_content)
    if char_count > CHAPTER_MAX_CHARS:
        async with session_maker() as s:
            ch = await s.get(Chapter, chapter_id)
            ch.status = STATUS_TOO_LONG
            await s.commit()
        yield _emit({"type": "status", "status": "too_long", "char_count": char_count})
        return

    if not LLM_API_KEY:
        yield _emit({"type": "error", "reason": "LLM_API_KEY 未配置"})
        return

    # 3) 标记为翻译中
    async with session_maker() as s:
        ch = await s.get(Chapter, chapter_id)
        ch.status = 1
        await s.commit()

    yield _emit({"type": "status", "status": "started", "char_count": char_count})

    # 4) 加载上一章英译 (上下文)
    async with session_maker() as s:
        prev_english = await _load_prev_chapter_english(s, book_id, chapter_index)

    # 5) 构造 prompt
    user_prompt = _build_user_prompt(
        chapter_title=chapter_title,
        chapter_zh=chapter_content,
        prev_chapter_english=prev_english,
    )

    # 6) 流式调用 LLM
    try:
        client = AsyncOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_API_BASE,
            timeout=120.0,
        )
        create_kwargs = dict(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=16000,
            stream=True,
        )
        if _IS_DEEPSEEK:
            create_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        stream = await client.chat.completions.create(**create_kwargs)
        full_text = ""
        chunk_count = 0
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                full_text += delta.content
                chunk_count += 1
                yield _emit({"type": "chunk", "text": delta.content})
                if chunk_count % 20 == 0:
                    yield _emit({"type": "progress", "chars": len(full_text)})

        if not full_text.strip():
            async with session_maker() as s:
                ch = await s.get(Chapter, chapter_id)
                ch.status = -1
                await s.commit()
            yield _emit({"type": "error", "reason": "empty response"})
            return

    except APIError as exc:
        print(f"[translator] chapter {chapter_id} API error: {exc!r}")
        async with session_maker() as s:
            ch = await s.get(Chapter, chapter_id)
            ch.status = -1
            await s.commit()
        yield _emit({"type": "error", "reason": f"API error: {exc!r}"})
        return
    except Exception as exc:
        print(f"[translator] chapter {chapter_id} unexpected error: {exc!r}")
        async with session_maker() as s:
            ch = await s.get(Chapter, chapter_id)
            ch.status = -1
            await s.commit()
        yield _emit({"type": "error", "reason": f"unexpected: {exc!r}"})
        return

    # 7) 写回 DB
    async with session_maker() as s:
        ch = await s.get(Chapter, chapter_id)
        ch.translated_content = full_text.strip()
        ch.status = 2
        total_done = (await s.execute(
            select(func.count(Chapter.id))
            .where(Chapter.book_id == book_id, Chapter.status == 2)
        )).scalar() or 0
        book = await s.get(Book, book_id)
        if book is not None:
            book.translated_chapters = total_done
        await s.commit()

    yield _emit({"type": "done", "text": full_text.strip()})
