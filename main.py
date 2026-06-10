"""
Vibe Reading - 沉浸式英语学习阅读
FastAPI 应用入口: 路由 + 启动 uvicorn (一键 python main.py)
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import APIError, AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session_maker, get_session, init_db
from models import Book, Chapter
from services.parser import parse_text, save_book_to_db
from services.settings import get_llm_config, load_settings, mask_api_key, save_settings
from services.translator import translate_chapter_stream


BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR = BASE_DIR / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Vibe Reading - 中英双语电子书阅读器", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------- 工具函数 ----------------------

def _chapter_status_label(chapter: Chapter) -> str:
    """根据 Chapter.status 返回状态字符串。"""
    max_chars = get_llm_config()["chapter_max_chars"]
    if len(chapter.content or "") > max_chars:
        return "too_long"
    if not chapter.content:
        return "empty"
    return {
        0: "pending",
        1: "in_progress",
        2: "done",
        -1: "failed",
        3: "too_long",
    }.get(chapter.status, "pending")


def _chapter_summary(ch: Chapter, prev_id: int | None, next_id: int | None) -> dict:
    """章节摘要 (不含 content)。"""
    return {
        "id": ch.id,
        "title": ch.title,
        "section": ch.section,
        "status": _chapter_status_label(ch),
        "char_count": len(ch.content or ""),
        "prev_chapter_id": prev_id,
        "next_chapter_id": next_id,
    }


# ---------------------- 路由 ----------------------

@app.get("/")
async def index(
    request: Request,
    session: AsyncSession = Depends(get_session),
    uploaded: int | None = None,
):
    stmt = select(Book).order_by(Book.created_at.desc())
    result = await session.execute(stmt)
    books = list(result.scalars().all())

    # 构建书籍列表，带上阅读进度信息
    books_with_progress = []
    for b in books:
        last_read_title = None
        if b.last_read_chapter_id:
            ch = await session.get(Chapter, b.last_read_chapter_id)
            if ch and ch.book_id == b.id:
                last_read_title = ch.title
        books_with_progress.append({
            "id": b.id,
            "title": b.title,
            "total_chapters": b.total_chapters,
            "translated_chapters": b.translated_chapters,
            "created_at": b.created_at,
            "last_read_chapter_id": b.last_read_chapter_id,
            "last_read_title": last_read_title,
        })

    flash = None
    if uploaded is not None:
        for b in books_with_progress:
            if b["id"] == uploaded:
                flash = {"id": b["id"], "title": b["title"]}
                break

    return templates.TemplateResponse(
        request,
        "index.html",
        {"books": books_with_progress, "flash": flash},
    )


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    filename = file.filename or "untitled.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 文件")

    raw = await file.read()
    text: str | None = None
    for enc in ("utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="ignore")

    chapters_data = parse_text(text)
    if not chapters_data:
        raise HTTPException(status_code=400, detail="未在文件中解析到任何内容")

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

    return RedirectResponse(url=f"/?uploaded={book.id}", status_code=303)


@app.post("/delete/{book_id}")
async def delete_book(
    book_id: int,
    session: AsyncSession = Depends(get_session),
):
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="书籍不存在")
    file_path = book.file_path
    from sqlalchemy import text
    await session.execute(text("DELETE FROM chapters WHERE book_id = :bid"), {"bid": book_id})
    await session.execute(text("DELETE FROM books WHERE id = :bid"), {"bid": book_id})
    await session.commit()
    try:
        Path(file_path).unlink(missing_ok=True)
    except OSError:
        pass
    return RedirectResponse(url="/", status_code=303)


@app.get("/read/{book_id}")
async def read(
    book_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="书籍不存在")

    chapters_stmt = (
        select(Chapter)
        .where(Chapter.book_id == book_id)
        .order_by(Chapter.chapter_index)
    )
    chapters = list((await session.execute(chapters_stmt)).scalars().all())

    chapter_list = [
        _chapter_summary(
            ch,
            prev_id=(chapters[i - 1].id if i > 0 else None),
            next_id=(chapters[i + 1].id if i + 1 < len(chapters) else None),
        )
        for i, ch in enumerate(chapters)
    ]

    chapter_max_chars = get_llm_config()["chapter_max_chars"]
    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "request": request,
            "book": book,
            "chapters": chapter_list,
            "chapter_max_chars": chapter_max_chars,
        },
    )


@app.get("/api/chapter/{chapter_id}")
async def get_chapter(
    chapter_id: int,
    session: AsyncSession = Depends(get_session),
):
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    next_stmt = (
        select(Chapter.id)
        .where(Chapter.book_id == chapter.book_id)
        .where(Chapter.chapter_index == chapter.chapter_index + 1)
    )
    next_chapter_id = (await session.execute(next_stmt)).scalar_one_or_none()

    return {
        "id": chapter.id,
        "title": chapter.title,
        "section": chapter.section,
        "content": chapter.content,
        "translated_content": chapter.translated_content,
        "status": _chapter_status_label(chapter),
        "char_count": len(chapter.content or ""),
        "next_chapter_id": next_chapter_id,
    }


@app.post("/translate/stream/{chapter_id}")
async def translate_stream(
    chapter_id: int,
    session: AsyncSession = Depends(get_session),
):
    """SSE 流式翻译端点。前端通过 fetch + ReadableStream 读取。"""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    status = _chapter_status_label(chapter)
    if status == "done":
        return {"type": "done", "text": chapter.translated_content}
    if status == "empty":
        return {"type": "error", "reason": "empty chapter"}
    if status == "in_progress":
        return {"type": "error", "reason": "in_progress"}

    async def event_generator():
        async for event_str in translate_chapter_stream(chapter_id, async_session_maker):
            yield f"data: {event_str}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/chapter-status/{book_id}")
async def chapter_status(
    book_id: int,
    session: AsyncSession = Depends(get_session),
):
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="书籍不存在")

    chapters_stmt = (
        select(Chapter)
        .where(Chapter.book_id == book_id)
        .order_by(Chapter.chapter_index)
    )
    chapters = list((await session.execute(chapters_stmt)).scalars().all())

    return {
        "book_id": book_id,
        "chapters": [
            {
                "id": ch.id,
                "title": ch.title,
                "status": _chapter_status_label(ch),
                "char_count": len(ch.content or ""),
            }
            for ch in chapters
        ],
    }


@app.post("/api/reading-progress/{book_id}")
async def save_reading_progress(
    book_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="书籍不存在")

    body = await request.json()
    chapter_id = body.get("chapter_id")
    if chapter_id is not None:
        chapter = await session.get(Chapter, chapter_id)
        if chapter is None or chapter.book_id != book_id:
            raise HTTPException(status_code=400, detail="章节不存在")

    book.last_read_chapter_id = chapter_id
    await session.commit()
    return {"ok": True}


@app.post("/api/reset-chapter/{chapter_id}")
async def reset_chapter(
    chapter_id: int,
    session: AsyncSession = Depends(get_session),
):
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    chapter.status = 0
    await session.commit()
    return {"ok": True}


# ---------------------- 设置 API ----------------------

@app.get("/api/settings")
async def get_settings():
    """返回当前设置 (API Key 脱敏)。"""
    s = load_settings()
    return {
        "api_key": mask_api_key(s.get("api_key", "")),
        "api_base": s.get("api_base", ""),
        "model": s.get("model", ""),
        "chapter_max_chars": s.get("chapter_max_chars", 20000),
    }


@app.post("/api/settings")
async def update_settings(request: Request):
    """保存 LLM 设置。"""
    body = await request.json()
    s = load_settings()

    api_key = body.get("api_key", "")
    # 如果前端提交的是脱敏值 (sk-***xxx), 则不覆盖
    masked = mask_api_key(s.get("api_key", ""))
    if api_key == masked:
        body.pop("api_key", None)

    save_settings(body)
    return {"ok": True}


@app.post("/api/settings/test-llm")
async def test_llm_connection():
    """测试当前 LLM 配置是否可用。"""
    config = get_llm_config()
    api_key = config["api_key"]
    api_base = config["api_base"]
    model = config["model"]

    if not api_key:
        return {"ok": False, "reason": "API Key 未配置"}

    try:
        client = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=30.0)
        create_kwargs = dict(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
            max_tokens=5,
            temperature=0,
        )
        if config["is_deepseek"]:
            create_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        response = await client.chat.completions.create(**create_kwargs)
        return {"ok": True, "model": model}
    except APIError as exc:
        return {"ok": False, "reason": f"API 错误: {exc.message}"}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


# ---------------------- 一键启动 ----------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
