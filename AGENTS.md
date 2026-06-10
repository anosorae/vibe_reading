# vibe_reading — Agent Guidance

## Dependencies & Running

- **uv** only. `uv sync` to install, `uv run main.py` to start.
- Server on `http://127.0.0.1:8000`, no hot reload.
- `uv.lock` committed — do not delete or regenerate.
- pip/venv fallback (`requirements.txt`) exists but prefer uv.

## Environment

- LLM config via Web UI settings modal → `settings.json` (gitignored).
- Default: `api_base` = `https://api.deepseek.com`, `model` = `deepseek-v4-flash`, `chapter_max_chars` = 20000.
- No `.env` file.

## Architecture

- Single FastAPI app in `main.py` — no routers, no `__init__.py` package.
- SQLite + SQLAlchemy 2 async (`aiosqlite`). Tables auto-created at startup; no migrations.
- DB file `vibe_reading.db` is gitignored. Uploads in `uploads/` (gitignored).
- Frontend: Jinja2 + TailwindCSS CDN + Alpine.js CDN. No npm/Vite.
- Translation: `AsyncOpenAI` SDK, streaming (`stream=True`). SSE via `POST /translate/stream/{id}`.
- Upload does NOT auto-translate. Translation is per-chapter, triggered on en mode navigation.

## Theme System

- Two themes via `data-theme` attribute on `<html>`: `vibe` (原木, warm brown) and `weread` (青简, muted green).
- All palette colors defined as CSS custom properties in `:root` and overridden in `[data-theme="weread"]`.
- Semantic utility classes replace Tailwind color utilities: `text-accent`, `text-primary`, `text-muted`, `bg-accent-subtle`, `border-divider`, `hover-text-accent`, etc.
- Tailwind config references CSS variables (`var(--color-xxx)`) so Tailwind classes (`text-charcoal`, `bg-sage-light`, etc.) also respond to theme.
- Theme persisted in `localStorage('vibe_theme')`; inline script in `<head>` reads it before paint to prevent FOUC.
- Theme toggle in navbar (homepage only; reader overrides `{% block nav %}` to hide nav).

## Key Features

- **Reading progress**: `books.last_read_chapter_id` saved on chapter navigation. Homepage shows "继续阅读". Reader restores last position on open.
- **Re-translation**: ↻ button on done/failed chapters. Calls `/api/reset-chapter` then triggers fresh translation.
- **Scroll-hide navbar**: Hides on scroll down, shows on scroll up. Alpine `x-show` + CSS transitions.
- **Chinese mode**: Hides translation status badge, translate button, and retry button.
- **Settings modal**: LLM config (API Key, Base, Model, max chars, context boost, thinking mode) + reading style (font size, font family, background color, theme). Test connection button.
- **Stream abort on navigation**: `AbortController` attached to translate fetch; `navigateTo()` aborts ongoing stream before switching chapters. Stream completion handler checks `activeChapterId` before loading chapter content.

## Key Conventions

- No Paragraph model. Book → Chapter only. Chapter stores `content` (original) and `translated_content` (English only).
- `chapters.status`: 0 = pending, 1 = in_progress, 2 = done, -1 = failed, 3 = too_long
- Chapters exceeding `chapter_max_chars` rejected outright (status=3). Value configurable via settings.
- Previous chapter's English translation used as context (truncated to 30K chars).
- LLM produces English-only translation with `[N]` paragraph markers. Frontend renders EN paragraphs; click to show original CN underneath.
- Two reading modes: 中文 (Chinese only, hides translation controls) and 英文 (English with click-to-show original).
- Backend `translate_chapter_stream` has `try/finally` that resets status from 1 to -1 on client disconnect (GeneratorExit/CancelledError).
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
