"""TXT 书籍解析: 按行拆段,正则识别章节。"""
from __future__ import annotations

import re
from typing import TypedDict

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import Book, Chapter, Paragraph


# 章节识别正则: 支持"第X章/回/节/卷/篇" 与 "Chapter X / CHAPTER X"
CHAPTER_PATTERN = re.compile(
    r"^\s*("
    r"第[\u4e00-\u9fa5\d]+[章回节卷篇]"  # 第一章 / 第1章
    r"|Chapter\s+\d+"                       # Chapter 1
    r"|CHAPTER\s+\d+"                       # CHAPTER 1
    r")\b"
)


class ChapterDict(TypedDict):
    title: str
    paragraphs: list[str]


def parse_text(content: str) -> list[ChapterDict]:
    """
    解析纯文本:
    - 每个非空行视为一个段落
    - 命中 CHAPTER_PATTERN 的行作为新章节标题
    - 若全文无章节标记, 则整本书归为一个 "全文" 章节
    """
    lines = content.splitlines()
    chapters: list[ChapterDict] = []
    current: ChapterDict | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        match = CHAPTER_PATTERN.match(line)
        if match:
            if current is not None and current["paragraphs"]:
                chapters.append(current)
            current = {"title": match.group(1).strip(), "paragraphs": []}
        else:
            if current is None:
                current = {"title": "全文", "paragraphs": []}
            current["paragraphs"].append(line)

    # 收尾
    if current is not None and current["paragraphs"]:
        chapters.append(current)

    return chapters


async def save_book_to_db(
    session: AsyncSession,
    title: str,
    file_path: str,
    chapters_data: list[ChapterDict],
) -> Book:
    """将解析结果写入数据库, 返回新建的 Book (含 id)。

    性能要点:
      - chapter / paragraph 全部走 executemany (单次往返插入多行)
      - 避免 ORM 单条 INSERT (10000 段从 ~10s 降到 ~300ms)
    """
    # 1) 建 Book 拿 id
    book = Book(
        title=title,
        file_path=file_path,
        total_paragraphs=sum(len(c["paragraphs"]) for c in chapters_data),
    )
    session.add(book)
    await session.flush()

    # 2) 批量插入 chapters (executemany)
    if chapters_data:
        chapter_dicts = [
            {
                "book_id": book.id,
                "title": ch["title"],
                "chapter_index": idx,
            }
            for idx, ch in enumerate(chapters_data)
        ]
        await session.execute(insert(Chapter), chapter_dicts)
        await session.flush()

        # 拉回 chapter.id (按 chapter_index 排序)
        from sqlalchemy import select
        ch_rows = (
            await session.execute(
                select(Chapter.id, Chapter.chapter_index)
                .where(Chapter.book_id == book.id)
                .order_by(Chapter.chapter_index)
            )
        ).all()
        chapter_ids_by_index = [row[0] for row in ch_rows]

        # 3) 批量插入 paragraphs (executemany)
        para_dicts: list[dict] = []
        for ch_idx, ch in enumerate(chapters_data):
            ch_id = chapter_ids_by_index[ch_idx]
            for p_idx, p_text in enumerate(ch["paragraphs"]):
                para_dicts.append(
                    {
                        "chapter_id": ch_id,
                        "paragraph_index": p_idx,
                        "original_text": p_text,
                        "translated_text": None,
                        "status": 0,
                    }
                )
        if para_dicts:
            # executemany: SQLite 单语句插入多行, 比 ORM 循环快 20-50x
            await session.execute(insert(Paragraph), para_dicts)

    await session.commit()
    await session.refresh(book)
    return book
