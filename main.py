"""
Vibe Reading - 中英双语电子书阅读器
FastAPI 应用入口: 路由 + 启动 uvicorn (一键 python main.py)
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import async_session_maker, get_session, init_db
from models import Book, Chapter, Paragraph
from services.parser import parse_text, save_book_to_db
from services.translator import translate_book_background


BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR = BASE_DIR / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期: 启动时建表。"""
    await init_db()
    yield


app = FastAPI(title="Vibe Reading - 中英双语电子书阅读器", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------- 工具函数 ----------------------

def _flatten_paragraphs(book: Book) -> list[dict]:
    """将 Book -> Chapter -> Paragraph 扁平化为一个 list[dict]。"""
    flat: list[dict] = []
    for chapter in sorted(book.chapters, key=lambda c: c.chapter_index):
        for p in sorted(chapter.paragraphs, key=lambda x: x.paragraph_index):
            flat.append(
                {
                    "id": p.id,
                    "chapter_title": chapter.title,
                    "original": p.original_text,
                    "translated": p.translated_text,
                    "status": p.status,
                }
            )
    return flat


# ---------------------- 路由 ----------------------

@app.get("/")
async def index(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """首页: 书架 + 上传表单。"""
    stmt = select(Book).order_by(Book.created_at.desc())
    result = await session.execute(stmt)
    books = list(result.scalars().all())
    return templates.TemplateResponse(
        request,
        "index.html",
        {"books": books},
    )


@app.post("/upload")
async def upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """上传 TXT: 落盘 -> 解析入库 -> 触发后台翻译 -> 跳转阅读器。"""
    filename = file.filename or "untitled.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 文件")

    raw = await file.read()
    # 尝试多种编码
    text: str | None = None
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "big5"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="ignore")

    chapters_data = parse_text(text)
    if not chapters_data:
        raise HTTPException(status_code=400, detail="未在文件中解析到任何段落")

    # 落盘
    safe_name = f"{uuid.uuid4().hex}_{Path(filename).name}"
    file_path = UPLOAD_DIR / safe_name
    file_path.write_bytes(raw)

    title = Path(filename).stem
    book = await save_book_to_db(
        session=session,
        title=title,
        file_path=str(file_path),
        chapters_data=chapters_data,
    )

    # 触发后台翻译
    background_tasks.add_task(translate_book_background, book.id, async_session_maker)

    return RedirectResponse(url=f"/read/{book.id}", status_code=303)


@app.get("/read/{book_id}")
async def read(
    book_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """阅读器页: 渲染 reader.html。"""
    stmt = (
        select(Book)
        .where(Book.id == book_id)
        .options(selectinload(Book.chapters).selectinload(Chapter.paragraphs))
    )
    result = await session.execute(stmt)
    book = result.scalar_one_or_none()
    if book is None:
        raise HTTPException(status_code=404, detail="书籍不存在")

    paragraphs = _flatten_paragraphs(book)
    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "book": book,
            "paragraphs": paragraphs,
        },
    )


@app.get("/api/progress/{book_id}")
async def progress(
    book_id: int,
    session: AsyncSession = Depends(get_session),
):
    """供前端轮询的翻译进度接口。"""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="书籍不存在")
    return {
        "book_id": book.id,
        "total": book.total_paragraphs,
        "translated": book.translated_count,
    }


# ---------------------- 一键启动 ----------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
