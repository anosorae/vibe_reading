# vibe_reading — Agent Guidance

## Dependencies & Running

- **uv** is the only supported dep manager. `uv sync` to install, `uv run main.py` to start.
- Server runs on `http://127.0.0.1:8000`, no hot reload.
- `uv.lock` is committed — do not delete or regenerate.
- pip/venv fallback exists (`requirements.txt`) but prefer uv.

## Environment

- LLM configuration is done via the Web UI settings modal (persisted to `settings.json`, gitignored).
- Default config: `api_base` = `https://api.deepseek.com`, `model` = `deepseek-v4-flash`, `chapter_max_chars` = 20000.
- `settings.json` is gitignored. No `.env` file needed.

## Architecture

- Single FastAPI app in `main.py` (no routers, no `__init__.py` package).
- SQLite + SQLAlchemy 2 async (`aiosqlite`). Tables auto-created at startup — no migrations.
- DB file `vibe_reading.db` is gitignored.
- Uploads stored in `uploads/` (gitignored).
- No npm/Vite — frontend is Jinja2 + TailwindCSS CDN + Alpine.js CDN.
- Translation calls LLM via OpenAI SDK (`AsyncOpenAI`) with streaming (`stream=True`). Supports any OpenAI-compatible provider. DeepSeek thinking mode auto-disabled when base_url contains "deepseek".
- Translation is streamed via SSE (`POST /translate/stream/{id}`). Frontend reads `ReadableStream`, displays tokens in real-time, then reloads chapter on completion.
- **Upload does NOT auto-translate.** Translation is per-chapter, triggered by user action or auto on navigation to en mode.

## Key Features

- **Reading progress**: Server-side storage (`books.last_read_chapter_id`). Saved on chapter navigation. Homepage shows "继续阅读" for books with progress. Reader auto-restores last position on open.
- **Re-translation**: ↻ button appears on done/failed chapters. Calls `/api/reset-chapter` to reset status to pending, then triggers fresh translation.
- **Scroll-hide navbar**: Navigation bar hides on scroll down, shows on scroll up. Uses Alpine.js `x-show` with CSS transitions.
- **Chinese mode**: Hides translation status badge, translate button, and retry button when in pure Chinese reading mode.
- **Settings modal**: LLM config (API Key, Base, Model, max chars) + reading style (font size, font family, background color). Test connection button validates API Key.

## Key Conventions

- No Paragraph model. Book → Chapter only. Chapter stores `content` (original) and `translated_content` (English only).
- `books.last_read_chapter_id`: nullable, references `chapters.id`, tracks reading position.
- `chapters.status`: 0 = pending, 1 = in_progress, 2 = done, -1 = failed, 3 = too_long
- Chapters > `CHAPTER_MAX_CHARS` chars are rejected outright (status=3). This value is configurable via settings.
- Previous chapter's English translation is used as context (truncated to 30K chars).
- LLM produces English-only translation with [N] paragraph markers. Frontend renders EN paragraphs; click any paragraph to show original CN underneath (lighter font).
- Two reading modes: 中文 (Chinese only, hides translation controls) and 英文 (English with click-to-show original).
- No test framework, no lint/typecheck config.
- All SQLite I/O uses executemany for bulk inserts.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Homepage with bookshelf |
| `POST` | `/upload` | Upload TXT file |
| `POST` | `/delete/{book_id}` | Delete book |
| `GET` | `/read/{book_id}` | Reader page |
| `GET` | `/api/chapter/{id}` | Get chapter data |
| `POST` | `/translate/stream/{id}` | SSE streaming translation |
| `POST` | `/api/reset-chapter/{id}` | Reset chapter status to pending |
| `POST` | `/api/reading-progress/{book_id}` | Save reading progress |
| `GET` | `/api/chapter-status/{book_id}` | Get all chapters' status |
| `GET` | `/api/settings` | Get LLM settings (API Key masked) |
| `POST` | `/api/settings` | Save LLM settings |
| `POST` | `/api/settings/test-llm` | Test LLM connection |

## Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

**Before implementing:** State your assumptions explicitly. If uncertain, ask. If multiple interpretations exist, present them — don't pick silently. If a simpler approach exists, say so. Push back when warranted. If something is unclear, stop. Name what's confusing. Ask.

**Simplicity First:** Minimum code that solves the problem. Nothing speculative. No features beyond what was asked. No abstractions for single-use code. No "flexibility" or "configurability" that wasn't requested. No error handling for impossible scenarios. If you write 200 lines and it could be 50, rewrite it. Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

**Surgical Changes:** Touch only what you must. Clean up only your own mess. When editing existing code: Don't "improve" adjacent code, comments, or formatting. Don't refactor things that aren't broken. Match existing style, even if you'd do it differently. If you notice unrelated dead code, mention it — don't delete it. When your changes create orphans: Remove imports/variables/functions that YOUR changes made unused. Don't remove pre-existing dead code unless asked. The test: Every changed line should trace directly to the user's request.
