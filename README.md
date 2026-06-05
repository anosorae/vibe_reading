# Vibe Reading — 中英双语电子书阅读器

一个本地部署的单体 Python Web 应用: 上传中文 TXT 电子书, 自动识别章节, 在后台调用 DeepSeek 逐段翻译为英文, 提供"中 / 英 / 双语"三种阅读模式。

零前端构建 (无 npm / Vite / Webpack), 一行命令启动。

---

## 特性

- **TXT 解析**: 正则识别"第 X 章 / 回 / 节 / 卷 / 篇"与"Chapter X", 按非空行拆段
- **异步翻译**: 默认**不自动翻译**, 用户在阅读器顶部点击"开始翻译"手动触发; FastAPI `BackgroundTasks` + `httpx` 调 DeepSeek, 滑动窗口取 N-1 / N-2 中文作语境, Prompt 明确"只输出当前段译文", `asyncio.Semaphore` 限流并发
- **三种阅读模式** (Alpine.js):
  - **纯中文**: 仅渲染中文段落
  - **纯英文**: 点击英文段落, `x-transition` 平滑展开对应中文 (手风琴)
  - **中英双语**: 中文 + 英文相邻渲染, 形成"上中下英"布局
- **实时进度**: 翻译状态每 3 秒自动刷新, 完成时自动停止轮询
- **零构建**: 模板 + Tailwind / Alpine CDN, 改完直接刷新浏览器

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | FastAPI · Jinja2 · SQLAlchemy 2 (async) · SQLite (aiosqlite) · httpx · python-dotenv |
| 前端 | 原生 HTML + Jinja2 模板 · TailwindCSS (CDN) · Alpine.js (CDN) |
| LLM | DeepSeek API (兼容 OpenAI Chat Completions 格式) |

---

## 快速开始

本项目使用 [uv](https://docs.astral.sh/uv/) 管理依赖与运行。安装 uv:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 1. 同步依赖

```bash
uv sync
```

`uv` 会自动创建 `.venv/` 虚拟环境并安装 `pyproject.toml` 中声明的所有依赖。`uv.lock` 保证跨机器一致版本, **已入库, 请勿删除**。

### 2. 配置环境变量

编辑 `.env`, 填入你的 DeepSeek API Key (申请: https://platform.deepseek.com/):

```ini
DEEPSEEK_API_KEY=sk-你的真实-key
DEEPSEEK_API_BASE=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
MAX_CONCURRENT_TRANSLATIONS=3
```

### 3. 启动

```bash
uv run main.py
```

浏览器打开 http://127.0.0.1:8000

数据库 `vibe_reading.db` 与上传目录 `uploads/` 首次启动时自动创建。

### 不使用 uv (备选)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

---

## 项目结构

```
vibe_reading/
├── main.py                  # FastAPI 入口 + 路由 + uvicorn 启动
├── database.py              # 异步 SQLAlchemy 引擎 / Session
├── models.py                # Book / Chapter / Paragraph ORM
├── services/
│   ├── __init__.py
│   ├── parser.py            # TXT 解析与入库
│   └── translator.py        # DeepSeek 调用、滑动窗口 Prompt、并发限流
├── templates/
│   ├── base.html            # 引入 Tailwind + Alpine.js CDN
│   ├── index.html           # 书架 + 上传表单
│   └── reader.html          # 三种阅读模式 (核心 UI)
├── static/                  # 静态资源 (预留)
├── uploads/                 # 上传的 TXT 落盘目录
├── pyproject.toml           # 项目元数据 + 依赖 (uv 读取)
├── uv.lock                  # 锁定依赖版本 (入库)
├── requirements.txt         # 备选, 不使用 uv 时用
├── .env                     # 环境变量 (不入库)
├── .gitignore
└── README.md
```

---

## 数据模型

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `books` | `id`, `title`, `file_path`, `total_paragraphs`, `translated_count`, `created_at` | 书籍 |
| `chapters` | `id`, `book_id`, `title`, `chapter_index` | 章节 |
| `paragraphs` | `id`, `chapter_id`, `paragraph_index`, `original_text`, `translated_text`, `status` | 段落 |

`paragraphs.status`: `0` = 待翻译 · `1` = 翻译中 · `2` = 完成 · `-1` = 失败

---

## 路由

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET`  | `/` | 首页 (书架 + 上传) |
| `POST` | `/upload` | 接收 TXT → 解析入库 → 303 跳转阅读器 (**不自动翻译**) |
| `POST` | `/translate/{book_id}` | 手动触发翻译; 幂等 (已全部完成/有段落进行中时直接返回) |
| `GET`  | `/read/{book_id}` | 渲染阅读器 |
| `GET`  | `/api/progress/{book_id}` | 返回 `{total, translated}`, 前端 3 秒轮询 |
| `GET`  | `/docs` | FastAPI 自动生成的 Swagger UI |

---

## 工作原理

### 解析流程 (`services/parser.py`)

1. 按 `\n` 切行, 跳过空行
2. 命中 `第X章/回/节/卷/篇 | Chapter X | CHAPTER X` 的行, 作为新章节标题 (使用匹配到的整段作 title, 避免吞掉第一段)
3. 其余非空行作为段落追加到当前章节
4. 全无章节标记时, 整本归为"全文"章节

### 翻译流程 (`services/translator.py`)

1. 拉取该书全部段落, 按 `(chapter_index, paragraph_index)` 排序
2. 对每段, 把 N-1 / N-2 的**中文原文** (非译文) 塞入 Prompt 上下文
3. `asyncio.Semaphore(MAX_CONCURRENT)` 限制并发, 避免触发 DeepSeek 限流
4. 翻译完成立即 UPDATE 段落, 刷新 `Book.translated_count` (供前端轮询)
5. **触发方式**: 上传时**不自动**启动; 由前端 `POST /translate/{book_id}` 手动触发, 后端用 `BackgroundTasks.add_task` 启动, 不阻塞响应

### 阅读器 (`templates/reader.html`)

- 顶层 `x-data="{ mode: 'zh', expandedId: null, translated, total }"`, 三个按钮 `@click="mode = '...'"` 切换
- 段落 DOM 顺序: **中文 `<p>` → 英文 `<p>` → 英文模式专用展开 `<div>`**
  - `mode = 'zh'`: 仅 `x-show` 中文 `<p>`
  - `mode = 'both'`: 中文 + 英文 `<p>` 都显示, 形成"上中下英"
  - `mode = 'en'`: 英文 `<p>` 可点击, `@click` 切换 `expandedId`, 展开 `<div>` 用 `x-transition` 渐入渐出
- `setInterval` 每 3 秒拉 `/api/progress` 刷新计数, 完成时 `clearInterval`

---

## 滑动窗口 Prompt 模板

```
SYSTEM:
你是一位资深中英双语文学翻译。
- 翻译必须自然流畅、地道, 保留原文语气、风格和文学性。
- 用户会提供前两段中文原文作为【语境参考】, 请勿翻译, 也不要重复输出。
- 只输出【当前段落】对应的英文译文, 不要输出任何解释、注释、标题或额外内容。

USER:
以下为前两段中文原文(仅作语境参考, 请勿翻译):
[1] {N-2 段中文}
[2] {N-1 段中文}

---

请翻译当前段落:
{N 段中文}

要求: 只输出当前段落的英文译文, 不要重复前文, 不要添加任何解释。
```

---

## 常见问题

**没有 DeepSeek API Key 能用吗?**
可以。上传 / 解析 / 中文阅读完全正常, 只是不会自动出英文。也可以手动把译文写进 `paragraphs.translated_text` 即可在英文 / 双语模式显示。

**想换 OpenAI / 智谱 / 自建网关?**
`.env` 改 `DEEPSEEK_API_BASE` 和 `DEEPSEEK_MODEL` 即可, 代码兼容任何 OpenAI Chat Completions 格式。

**端口 8000 被占用?**
改 `main.py` 末尾 `uvicorn.run(..., port=8000)` 即可。

**翻译失败的段落怎么办?**
`status` 标记为 `-1`, 前端显示"⟳ 翻译中…"。可在 SQLite 里 `UPDATE paragraphs SET status = 0, translated_text = NULL WHERE id = ?` 然后重启服务, 翻译任务会跳过已有译文重新拉一次 (目前未实现重试队列, 简单方案是删除该书重新上传)。

**支持 EPUB / MOBI / PDF 吗?**
目前只支持纯 TXT。EbookLib (epub) 容易接入; PDF 需要 pdfplumber + 排版还原, 工作量较大, 见后续路线。

---

## 路线图

- [ ] 章节级翻译断点续传, 中途重启不丢工
- [ ] EPUB / MOBI 解析
- [ ] 段落选中后单段重译
- [ ] 翻译术语表 (glossary) 注入 Prompt
- [ ] 用户账号与多设备同步

---

## 许可

MIT
