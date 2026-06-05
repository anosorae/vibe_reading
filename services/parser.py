"""TXT 书籍解析: 按行拆段,正则识别章节。"""
from __future__ import annotations

import re
from typing import TypedDict

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
    """将解析结果写入数据库, 返回新建的 Book (含 id)。"""
    book = Book(
        title=title,
        file_path=file_path,
        total_paragraphs=sum(len(c["paragraphs"]) for c in chapters_data),
    )
    session.add(book)
    await session.flush()  # 拿到 book.id

    for ch_idx, ch_data in enumerate(chapters_data):
        chapter = Chapter(
            book_id=book.id,
            title=ch_data["title"],
            chapter_index=ch_idx,
        )
        session.add(chapter)
        await session.flush()

        for p_idx, p_text in enumerate(ch_data["paragraphs"]):
            session.add(
                Paragraph(
                    chapter_id=chapter.id,
                    paragraph_index=p_idx,
                    original_text=p_text,
                    translated_text=None,
                    status=0,
                )
            )

    await session.commit()
    await session.refresh(book)
    return book
