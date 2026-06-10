"""
按章翻译服务 (OpenAI 兼容格式)。

设计要点:
  - 粒度: 一章 = 一次 API 调用
  - 输出: 中文段落 + 分隔线 + 英文段落 (双语对照)
  - 上下文: 可选, 前 N 章译文 (总字符受 context_max_chars 限制)
  - 超长拒绝: 单章字符 > CHAPTER_MAX_CHARS 直接拒绝, 标记 chapter.status=3
  - 客户端: openai.AsyncOpenAI (官方 SDK, OpenAI 兼容格式)
  - 流式输出: translate_chapter_stream() 逐 token 输出, 前端实时显示
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Optional

from openai import APIError, AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from models import Book, Chapter
from services.settings import get_llm_config

STATUS_TOO_LONG = 3

SYSTEM_PROMPT = """你是一位资深中英文学翻译。
将用户给定的整章中文翻译为英文, 保留原文语气、风格、文学性。

源文中的每个段落以 [1], [2], [3] 等标记开头。你必须在英文译文中保留完全相同的段落标记, 使得每个标记段落与原文一一对应。

输出格式:
[1] 第一段的英文译文[2] 第二段的英文译文
...

严禁输出任何解释、标题、注释或额外内容。"""


def _build_user_prompt(
    chapter_title: str,
    chapter_zh: str,
    prev_chapter_english: Optional[str],
) -> str:
    paragraphs = [p.strip() for p in chapter_zh.split("\n\n") if p.strip()]
    marked_text = "\n\n".join(f"[{i+1}] {p}" for i, p in enumerate(paragraphs))
    parts: list[str] = []
    if prev_chapter_english:
        parts.append("上一章英译 (供术语 / 风格衔接参考):")
        parts.append(prev_chapter_english)
        parts.append("\n---\n")
    parts.append(f"Chapter: {chapter_title}")
    parts.append("请将以下整章中文翻译为英文, 保留每个段落的 [N] 标记:")
    parts.append(marked_text)
    return "\n".join(parts)


def _truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n\n[... middle truncated ...]\n\n{text[-half:]}"


async def _load_prev_chapters_english(
    session: AsyncSession,
    book_id: int,
    chapter_index: int,
    count: int,
    available_chars: int,
) -> Optional[str]:
    """加载前 count 章译文, 按时间顺序拼接, 总字符截断到 available_chars。"""
    if chapter_index <= 0 or count <= 0 or available_chars <= 0:
        return None
    parts = []
    for i in range(1, count + 1):
        idx = chapter_index - i
        if idx < 0:
            break
        stmt = select(Chapter.translated_content).where(
            Chapter.book_id == book_id,
            Chapter.chapter_index == idx,
            Chapter.status == 2,
        )
        result = (await session.execute(stmt)).scalar_one_or_none()
        if result:
            parts.append(result)
    if not parts:
        return None
    combined = "\n\n---\n\n".join(reversed(parts))
    if len(combined) > available_chars:
        half = available_chars // 2
        combined = f"{combined[:half]}\n\n[... middle truncated ...]\n\n{combined[-half:]}"
    return combined


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
    config = get_llm_config()
    api_key = config["api_key"]
    api_base = config["api_base"]
    model = config["model"]
    chapter_max_chars = config["chapter_max_chars"]
    enable_context_boost = config["enable_context_boost"]
    context_chapters = config["context_chapters"]
    context_max_chars = config["context_max_chars"]
    enable_thinking = config["enable_thinking"]

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
    if char_count > chapter_max_chars:
        print(f"[translator] chapter {chapter_id} 过长: {char_count} chars > {chapter_max_chars}")
        async with session_maker() as s:
            ch = await s.get(Chapter, chapter_id)
            ch.status = STATUS_TOO_LONG
            await s.commit()
        return {"status": "too_long", "char_count": char_count}

    if not api_key:
        return {"status": "skipped", "reason": "LLM_API_KEY 未配置"}

    # 3) 标记为翻译中
    async with session_maker() as s:
        ch = await s.get(Chapter, chapter_id)
        ch.status = 1
        await s.commit()

    # 4) 加载上下文
    prev_english = None
    if enable_context_boost:
        budget = context_max_chars - char_count
        if budget > 0:
            async with session_maker() as s:
                prev_english = await _load_prev_chapters_english(
                    s, book_id, chapter_index,
                    context_chapters, budget,
                )

    # 5) 构造 prompt
    user_prompt = _build_user_prompt(
        chapter_title=chapter_title,
        chapter_zh=chapter_content,
        prev_chapter_english=prev_english,
    )

    # 6) 调用 LLM
    try:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base,
            timeout=120.0,
        )
        create_kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=16000,
        )
        create_kwargs["extra_body"] = {
            "thinking": {"type": "enabled" if enable_thinking else "disabled"}
        }
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
    config = get_llm_config()
    api_key = config["api_key"]
    api_base = config["api_base"]
    model = config["model"]
    chapter_max_chars = config["chapter_max_chars"]
    enable_context_boost = config["enable_context_boost"]
    context_chapters = config["context_chapters"]
    context_max_chars = config["context_max_chars"]
    enable_thinking = config["enable_thinking"]

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
    if char_count > chapter_max_chars:
        async with session_maker() as s:
            ch = await s.get(Chapter, chapter_id)
            ch.status = STATUS_TOO_LONG
            await s.commit()
        yield _emit({"type": "status", "status": "too_long", "char_count": char_count})
        return

    if not api_key:
        yield _emit({"type": "error", "reason": "LLM_API_KEY 未配置"})
        return

    # 3) 标记为翻译中
    async with session_maker() as s:
        ch = await s.get(Chapter, chapter_id)
        ch.status = 1
        await s.commit()

    yield _emit({"type": "status", "status": "started", "char_count": char_count})

    # 4) 加载上下文
    prev_english = None
    if enable_context_boost:
        budget = context_max_chars - char_count
        if budget > 0:
            async with session_maker() as s:
                prev_english = await _load_prev_chapters_english(
                    s, book_id, chapter_index,
                    context_chapters, budget,
                )

    # 5) 构造 prompt
    user_prompt = _build_user_prompt(
        chapter_title=chapter_title,
        chapter_zh=chapter_content,
        prev_chapter_english=prev_english,
    )

    # 6) 流式调用 LLM
    try:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base,
            timeout=120.0,
        )
        create_kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=16000,
            stream=True,
        )
        create_kwargs["extra_body"] = {
            "thinking": {"type": "enabled" if enable_thinking else "disabled"}
        }

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
