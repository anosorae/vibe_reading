# vibe_reading — Agent Guidance

## Dependencies & Running

- **uv** is the only supported dep manager. `uv sync` to install, `uv run main.py` to start.
- Server runs on `http://127.0.0.1:8000`, no hot reload.
- `uv.lock` is committed — do not delete or regenerate.
- pip/venv fallback exists (`requirements.txt`) but prefer uv.

## Environment

- Copy `.env.example` or edit `.env` with real `DEEPSEEK_API_KEY`.
- Key vars: `DEEPSEEK_API_KEY`, `DEEPSEEK_API_BASE`, `DEEPSEEK_MODEL`, `CHAPTER_MAX_CHARS` (default 20000).
- `.env` is gitignored.

## Architecture

- Single FastAPI app in `main.py` (no routers, no `__init__.py` package).
- SQLite + SQLAlchemy 2 async (`aiosqlite`). Tables auto-created at startup — no migrations.
- DB file `vibe_reading.db` is gitignored.
- Uploads stored in `uploads/` (gitignored).
- No npm/Vite — frontend is Jinja2 + TailwindCSS CDN + Alpine.js CDN.
- Translation calls DeepSeek via OpenAI SDK (`AsyncOpenAI`). Thinking mode disabled.
- Translation runs via `BackgroundTasks.add_task` (in-process, not celery/redis). Restarting the server mid-translation loses in-progress work.
- **Upload does NOT auto-translate.** Translation is per-chapter, triggered by user action.

## Key Conventions

- `paragraphs.status`: 0 = pending, 1 = in_progress, 2 = done, -1 = failed, 3 = too_long
- Chapters > `CHAPTER_MAX_CHARS` chars are rejected outright (status=3 on all paragraphs).
- Previous chapter's English translation is used as context (truncated to 30K chars, head+tail half each).
- No test framework, no lint/typecheck config.
- All SQLite I/O uses executemany for bulk inserts (never ORM loop for paragraphs).

## Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

**Before implementing:** State your assumptions explicitly. If uncertain, ask. If multiple interpretations exist, present them — don't pick silently. If a simpler approach exists, say so. Push back when warranted. If something is unclear, stop. Name what's confusing. Ask.

**Simplicity First:** Minimum code that solves the problem. Nothing speculative. No features beyond what was asked. No abstractions for single-use code. No "flexibility" or "configurability" that wasn't requested. No error handling for impossible scenarios. If you write 200 lines and it could be 50, rewrite it. Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

**Surgical Changes:** Touch only what you must. Clean up only your own mess. When editing existing code: Don't "improve" adjacent code, comments, or formatting. Don't refactor things that aren't broken. Match existing style, even if you'd do it differently. If you notice unrelated dead code, mention it — don't delete it. When your changes create orphans: Remove imports/variables/functions that YOUR changes made unused. Don't remove pre-existing dead code unless asked. The test: Every changed line should trace directly to the user's request.
