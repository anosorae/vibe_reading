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
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import async_session_maker, get_session, init_db
from models import Book, Chapter, Paragraph
from services.parser import parse_text, save_book_to_db
from services.translator import CHAPTER_MAX_CHARS, translate_chapter


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

_EMPTY_AGG = {
    "paragraph_count": 0,
    "char_count": 0,
    "status_in_progress": 0,
    "status_done": 0,
    "status_failed": 0,
    "status_too_long": 0,
}


async def _book_aggregates(session: AsyncSession, book_id: int) -> dict[int, dict]:
    """一次 SQL 聚合: 该书所有章节的 (段落数, 字符数, 各 status 的段数)。

    替代原来 selectinload(Chapter.paragraphs) 的 ORM 循环:
      - 旧: 加载 N 个 Paragraph 对象到 Python, 逐个求 len(original_text)  (10000 段 ~ 300ms)
      - 新: 一次 SQL GROUP BY 聚合                                    (10000 段 ~ 5-15ms)
    """
    pc = func.count().label("pc")
    cc = func.coalesce(func.sum(func.length(Paragraph.original_text)), 0).label("cc")
    s1 = func.coalesce(func.sum(case((Paragraph.status == 1, 1), else_=0)), 0).label("s1")
    s2 = func.coalesce(func.sum(case((Paragraph.status == 2, 1), else_=0)), 0).label("s2")
    sn = func.coalesce(func.sum(case((Paragraph.status == -1, 1), else_=0)), 0).label("sn")
    s3 = func.coalesce(func.sum(case((Paragraph.status == 3, 1), else_=0)), 0).label("s3")

    stmt = (
        select(
            Paragraph.chapter_id,
            pc, cc, s1, s2, sn, s3,
        )
        .where(Paragraph.chapter_id.in_(
            select(Chapter.id).where(Chapter.book_id == book_id)
        ))
        .group_by(Paragraph.chapter_id)
    )
    rows = (await session.execute(stmt)).all()
    return {
        r[0]: {
            "paragraph_count": int(r[1] or 0),
            "char_count": int(r[2] or 0),
            "status_in_progress": int(r[3] or 0),
            "status_done": int(r[4] or 0),
            "status_failed": int(r[5] or 0),
            "status_too_long": int(r[6] or 0),
        }
        for r in rows
    }


async def _chapter_aggregate(session: AsyncSession, chapter_id: int) -> dict:
    """单章的聚合 (复用 _book_aggregates 也行, 单独写是更精确的小查询)。"""
    pc = func.count()
    cc = func.coalesce(func.sum(func.length(Paragraph.original_text)), 0)
    s1 = func.coalesce(func.sum(case((Paragraph.status == 1, 1), else_=0)), 0)
    s2 = func.coalesce(func.sum(case((Paragraph.status == 2, 1), else_=0)), 0)
    sn = func.coalesce(func.sum(case((Paragraph.status == -1, 1), else_=0)), 0)
    s3 = func.coalesce(func.sum(case((Paragraph.status == 3, 1), else_=0)), 0)
    row = (await session.execute(
        select(pc, cc, s1, s2, sn, s3).where(Paragraph.chapter_id == chapter_id)
    )).one()
    return {
        "paragraph_count": int(row[0] or 0),
        "char_count": int(row[1] or 0),
        "status_in_progress": int(row[2] or 0),
        "status_done": int(row[3] or 0),
        "status_failed": int(row[4] or 0),
        "status_too_long": int(row[5] or 0),
    }


def _chapter_status_from_agg(chapter_id: int, title: str, agg: dict) -> dict:
    """用聚合 dict 算章节 status 字符串 (替代原来 ORM 循环 + len(original_text))。"""
    pc = agg["paragraph_count"]
    cc = agg["char_count"]
    if cc > CHAPTER_MAX_CHARS:
        status = "too_long"
    elif pc == 0:
        status = "empty"
    else:
        s1 = agg["status_in_progress"]
        s2 = agg["status_done"]
        sn = agg["status_failed"]
        s3 = agg["status_too_long"]
        if s1 > 0:
            status = "in_progress"
        elif s2 == pc:
            status = "done"
        elif (sn + s3) == pc:
            status = "failed"
        elif s2 > 0 and (sn + s3) > 0:
            status = "partial"
        elif s2 > 0:
            status = "in_progress"
        else:
            status = "pending"
    return {
        "id": chapter_id,
        "title": title,
        "status": status,
        "char_count": cc,
        "paragraph_count": pc,
        "translated_count": agg["status_done"],
    }


def _chapter_meta_from_agg(chapter_id: int, title: str, prev_chapter_id: int | None, next_chapter_id: int | None, agg: dict) -> dict:
    return {
        "id": chapter_id,
        "title": title,
        "char_count": agg["char_count"],
        "paragraph_count": agg["paragraph_count"],
        "prev_chapter_id": prev_chapter_id,
        "next_chapter_id": next_chapter_id,
    }


def _build_chapter_full(chapter: Chapter, next_chapter_id: int | None) -> dict:
    """章节完整数据 (含 paragraphs), 用于懒加载接口 /api/chapter/{id}。

    注意: 这个接口是用户主动点进章节才触发, 加载段落文本是必要的。
    """
    paragraphs = sorted(chapter.paragraphs, key=lambda p: p.paragraph_index)
    return {
        "id": chapter.id,
        "title": chapter.title,
        "char_count": sum(len(p.original_text or "") for p in paragraphs),
        "paragraph_count": len(paragraphs),
        "next_chapter_id": next_chapter_id,
        "paragraphs": [
            {
                "id": p.id,
                "original": p.original_text,
                "translated": p.translated_text,
                "status": p.status,
            }
            for p in paragraphs
        ],
    }


# ---------------------- 路由 ----------------------

@app.get("/")
async def index(
    request: Request,
    session: AsyncSession = Depends(get_session),
    uploaded: int | None = None,
):
    """首页: 书架 + 上传表单。

    `?uploaded={book_id}` 用于显示"✓ 已上传《xxx》"闪一下 (upload 完跳回 / 时带这个 query)。
    """
    stmt = select(Book).order_by(Book.created_at.desc())
    result = await session.execute(stmt)
    books = list(result.scalars().all())

    flash = None
    if uploaded is not None:
        for b in books:
            if b.id == uploaded:
                flash = {"id": b.id, "title": b.title}
                break

    return templates.TemplateResponse(
        request,
        "index.html",
        {"books": books, "flash": flash},
    )


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """上传 TXT: 落盘 -> 解析入库 -> 跳回书架 (不直接进阅读器, 避免大书卡浏览器)。

    跳回时带 ?uploaded={id} 给前端一个"已上传"提示。
    """
    filename = file.filename or "untitled.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 文件")

    raw = await file.read()
    # 优先 UTF-8 (90%+ 中文 TXT 是 UTF-8), 失败再试 GBK; 全失败则 ignore
    # 不再依次试 utf-8-sig / gb18030 / big5, 节省 CPU + 内存
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
        raise HTTPException(status_code=400, detail="未在文件中解析到任何段落")

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

    # 跳回书架, 用 query 标记本次上传, 前端渲染闪一下
    return RedirectResponse(url=f"/?uploaded={book.id}", status_code=303)


@app.post("/delete/{book_id}")
async def delete_book(
    book_id: int,
    session: AsyncSession = Depends(get_session),
):
    """删除书籍: raw SQL 批量删 (paragraphs → chapters → book) + 删上传文件。"""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="书籍不存在")
    file_path = book.file_path
    # raw SQL 批量删除, 比 ORM cascade 快 100x
    from sqlalchemy import text
    await session.execute(text("DELETE FROM paragraphs WHERE chapter_id IN (SELECT id FROM chapters WHERE book_id = :bid)"), {"bid": book_id})
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
    """阅读器页: 渲染 reader.html, 只取章节元信息 (不加载 paragraphs)。

    段落文本通过 GET /api/chapter/{id} 懒加载, 避免首屏把全书 HTML 塞给浏览器。
    char_count / paragraph_count 用一次 SQL 聚合查, 不用 ORM 循环 (5000 段从 300ms → 5-15ms)。
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="书籍不存在")

    chapters_stmt = (
        select(Chapter)
        .where(Chapter.book_id == book_id)
        .order_by(Chapter.chapter_index)
    )
    chapters = list((await session.execute(chapters_stmt)).scalars().all())

    agg = await _book_aggregates(session, book_id)
    chapter_list = [
        _chapter_meta_from_agg(
            ch.id, ch.title,
            prev_chapter_id=(chapters[i - 1].id if i > 0 else None),
            next_chapter_id=(chapters[i + 1].id if i + 1 < len(chapters) else None),
            agg=agg.get(ch.id, _EMPTY_AGG),
        )
        for i, ch in enumerate(chapters)
    ]

    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "request": request,
            "book": book,
            "chapters": chapter_list,
            "chapter_max_chars": CHAPTER_MAX_CHARS,
        },
    )


@app.get("/api/chapter/{chapter_id}")
async def get_chapter(
    chapter_id: int,
    session: AsyncSession = Depends(get_session),
):
    """懒加载接口: 用户主动点进章节才触发, 这里必须加载段落文本。"""
    stmt = (
        select(Chapter)
        .where(Chapter.id == chapter_id)
        .options(selectinload(Chapter.paragraphs))
    )
    chapter = (await session.execute(stmt)).scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    next_stmt = (
        select(Chapter.id)
        .where(Chapter.book_id == chapter.book_id)
        .where(Chapter.chapter_index == chapter.chapter_index + 1)
    )
    next_chapter_id = (await session.execute(next_stmt)).scalar_one_or_none()

    return _build_chapter_full(chapter, next_chapter_id)


@app.post("/translate/chapter/{chapter_id}")
async def trigger_chapter_translate(
    chapter_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """
    触发单章翻译。返回值:
      - 200 {status: "started", "char_count": N}  启动成功
      - 200 {status: "in_progress"}                该章已在翻译中
      - 200 {status: "done"}                       该章已全部完成
      - 200 {status: "empty"}                      该章无段落
      - 200 {status: "too_long", "char_count": N}  超长拒绝
      - 404                                        章节不存在

    性能: 不再 selectinload(Chapter.paragraphs), 用单条 SQL 聚合查 (pc, cc, 各 status 段数)。
    too_long 标记用单条 UPDATE 一次性写完, 避免循环 ORM。
    """
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    agg = await _chapter_aggregate(session, chapter_id)
    pc = agg["paragraph_count"]
    if pc == 0:
        return {"status": "empty"}
    if agg["status_in_progress"] > 0:
        return {"status": "in_progress"}
    if agg["status_done"] == pc:
        return {"status": "done"}

    cc = agg["char_count"]
    if cc > CHAPTER_MAX_CHARS:
        # 单条 UPDATE 一次性把所有段落标 3
        await session.execute(
            update(Paragraph).where(Paragraph.chapter_id == chapter_id).values(status=3)
        )
        await session.commit()
        return {"status": "too_long", "char_count": cc}

    background_tasks.add_task(translate_chapter, chapter.id, async_session_maker)
    return {"status": "started", "char_count": cc}


@app.get("/api/chapter-status/{book_id}")
async def chapter_status(
    book_id: int,
    session: AsyncSession = Depends(get_session),
):
    """前端轮询: 返回每章的状态聚合。"""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="书籍不存在")

    chapters_stmt = (
        select(Chapter)
        .where(Chapter.book_id == book_id)
        .order_by(Chapter.chapter_index)
    )
    chapters = list((await session.execute(chapters_stmt)).scalars().all())

    agg = await _book_aggregates(session, book_id)
    return {
        "book_id": book_id,
        "chapters": [
            _chapter_status_from_agg(ch.id, ch.title, agg.get(ch.id, _EMPTY_AGG))
            for ch in chapters
        ],
    }


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
