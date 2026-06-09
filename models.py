"""SQLAlchemy 数据模型: Book / Chapter。"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(String(1024))
    total_chapters: Mapped[int] = mapped_column(Integer, default=0)
    translated_chapters: Mapped[int] = mapped_column(Integer, default=0)
    last_read_chapter_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    chapters: Mapped[list["Chapter"]] = relationship(
        "Chapter",
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="Chapter.chapter_index",
    )


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(512))
    chapter_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="")
    translated_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 0:待翻译, 1:翻译中, 2:已完成, -1:失败, 3:too_long
    status: Mapped[int] = mapped_column(Integer, default=0)

    book: Mapped["Book"] = relationship("Book", back_populates="chapters")
