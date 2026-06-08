"""TXT 书籍解析: 按章节正则分割, 保留原始文本内容。"""
from __future__ import annotations

import re
from typing import TypedDict

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Book, Chapter

# 章级标记 (仅标记本身, 不含后续标题): 用于判断标题行是否"只有标记"
_MARKER_ONLY = re.compile(
    r"^第(?:[\u4e00-\u9fa5零一二三四五六七八九十百千万]+|\d+)[章回节卷]$"
    r"|^Chapter\s+\d+$"
    r"|^CHAPTER\s+\d+$",
    re.IGNORECASE,
)
CHAPTER_PATTERN = re.compile(
    r"^\s*(第(?:[\u4e00-\u9fa5零一二三四五六七八九十百千万]+|\d+)[章回节卷](?:\s+.+?)?)\s*$"
    r"|^\s*(Chapter\s+\d+.*)$"
    r"|^\s*(CHAPTER\s+\d+.*)$",
    re.IGNORECASE,
)

# 篇级标记: 第X篇/第X卷 — 更大的结构单元, 不作为章节分割边界
SECTION_PATTERN = re.compile(
    r"^\s*(第(?:[\u4e00-\u9fa5零一二三四五六七八九十百千万]+|\d+)[篇](?:\s+.+?)?)\s*$"
)


class ChapterDict(TypedDict):
    title: str
    content: str


def _extract_title(match: re.Match) -> str:
    """从正则 match 中提取标题 (取第一个非 None 的 group)。"""
    for g in match.groups():
        if g:
            return g.strip()
    return ""


def _join_paragraphs(lines: list[str]) -> str:
    """将行列表合并为段落文本: 每个非空行作为一段, 段间用双换行分隔。"""
    paragraphs = [line.strip() for line in lines if line.strip()]
    return "\n\n".join(paragraphs)


def parse_text(content: str) -> list[ChapterDict]:
    """
    解析纯文本:
    - 第X章/回/节/卷 作为章节分割边界, 整行作为标题
    - 第X篇 作为更高级结构标记, 不分割章节
    - 篇名会自动添加到其后第一个章节的标题前面 (如 "第一篇 一夜觉醒 / 第1章 罗峰")
    - 若标题行只有标记 (如"第一章"), 尝试用下一行非空文本作为标题补充
    - 前文作为独立"序章"章节
    - 若全文无章节标记, 则整本书归为一个"全文"章节
    - 每章的 content 保留原始文本 (含换行)
    """
    lines = content.splitlines()
    chapters: list[ChapterDict] = []
    current_title: str | None = None
    current_lines: list[str] = []
    preamble_lines: list[str] = []
    found_chapter = False
    pending_section: str | None = None  # 待合并到下一个章节标题的篇名

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line:
            if current_title is not None:
                current_lines.append("")
            else:
                preamble_lines.append("")
            i += 1
            continue

        # 先检查是否是章级标记
        ch_match = CHAPTER_PATTERN.match(line)
        if ch_match:
            found_chapter = True
            # 保存前一章
            if current_title is not None:
                chapters.append({
                    "title": current_title,
                    "content": _join_paragraphs(current_lines),
                })
            elif preamble_lines:
                chapters.append({
                    "title": "序章",
                    "content": _join_paragraphs(preamble_lines),
                })
                preamble_lines = []

            title = _extract_title(ch_match)

            # 如果标题只有标记 (如"第一章", "第1章"), 尝试用下一行作为标题补充
            if _MARKER_ONLY.match(title):
                peek = i + 1
                while peek < len(lines) and not lines[peek].strip():
                    peek += 1
                if peek < len(lines):
                    next_line = lines[peek].strip()
                    # 确保下一行不是标记、不是缩进内容、不是长文本
                    if (next_line
                            and not CHAPTER_PATTERN.match(next_line)
                            and not SECTION_PATTERN.match(next_line)
                            and not lines[peek][:1].isspace()  # 缩进的是正文不是标题
                            and len(next_line) <= 20):          # 标题通常较短
                        title = f"{title} {next_line}"
                        i = peek

            # 如果有待合并的篇名, 添加到标题前面
            if pending_section:
                title = f"{pending_section} / {title}"
                pending_section = None

            current_title = title
            current_lines = []
            i += 1
            continue

        # 篇级标记: 不分割章节, 记录为待合并篇名
        sec_match = SECTION_PATTERN.match(line)
        if sec_match:
            pending_section = _extract_title(sec_match)
            i += 1
            continue

        # 普通文本
        if current_title is not None:
            current_lines.append(raw)
        else:
            preamble_lines.append(raw)
        i += 1

    # 最后一章
    if current_title is not None:
        # 如果还有未合并的篇名, 也加上
        if pending_section:
            current_title = f"{pending_section} / {current_title}"
        chapters.append({
            "title": current_title,
            "content": _join_paragraphs(current_lines),
        })
    elif not found_chapter and preamble_lines:
        # 如果只有篇名没有章, 篇名作为标题
        title = pending_section if pending_section else "全文"
        chapters.append({
            "title": title,
            "content": _join_paragraphs(preamble_lines),
        })

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
        total_chapters=len(chapters_data),
    )
    session.add(book)
    await session.flush()

    if chapters_data:
        chapter_dicts = [
            {
                "book_id": book.id,
                "title": ch["title"],
                "chapter_index": idx,
                "content": ch["content"],
                "translated_content": None,
                "status": 0,
            }
            for idx, ch in enumerate(chapters_data)
        ]
        await session.execute(insert(Chapter), chapter_dicts)

    await session.commit()
    await session.refresh(book)
    return book
