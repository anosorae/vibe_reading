# Vibe Reading — 中英双语电子书阅读器

一个本地部署的单体 Python Web 应用: 上传中文 TXT 电子书, 自动识别章节, 逐**章**调用 LLM 翻译为中英双语对照, 提供"中 / 英 / 双语"三种阅读模式。

零前端构建 (无 npm / Vite / Webpack), 一行命令启动。

---

## 特性

- **TXT 解析**: 正则识别"第 X 章 / 回 / 节 / 卷 / 篇"与"Chapter X", 按章节分割, 保留原始文本
- **按章翻译 (流式)**: 上传后**默认停在纯中文模式**; 切换到英文时, 自动触发翻译, LLM 响应逐 token 流式输出, 实时可见翻译进度
  - LLM 输出纯英文译文, 段落与原文一一对应
  - 单章 > 20K 字符直接拒绝翻译 (返回 `too_long`, 不降级)
  - 上一章英译作为**语境** (Prompt 上下文), 超 30K 字符自动截取头尾各半
- **两种阅读模式** (Alpine.js):
  - **纯中文**: 仅渲染中文段落
  - **英文**: 仅渲染英文译文, 点击任意段落显示对应原文 (淡色字体)
- **翻译流式输出**: SSE 流式传输 LLM 响应, 逐 token 实时显示, 翻译完成后自动渲染双语对照
- **零构建**: 模板 + Tailwind / Alpine CDN, 改完直接刷新浏览器

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | FastAPI · Jinja2 · SQLAlchemy 2 (async) · SQLite (aiosqlite) · openai (官方 SDK) · python-dotenv |
| 前端 | 原生 HTML + Jinja2 模板 · TailwindCSS (CDN) · Alpine.js (CDN) |
| LLM | OpenAI Chat Completions 兼容格式 (DeepSeek / 通义千问 / 智谱 / Ollama 等) |

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

编辑 `.env`, 填入你的 LLM API Key:

```ini
LLM_API_KEY=sk-你的真实-key
LLM_API_BASE=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
CHAPTER_MAX_CHARS=20000
```

支持任何 OpenAI Chat Completions 兼容接口:

| 提供方 | LLM_API_BASE | LLM_MODEL |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` |

> 也兼容旧变量名 `DEEPSEEK_API_KEY` / `DEEPSEEK_API_BASE` / `DEEPSEEK_MODEL`, 优先读 `LLM_*`。

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
├── models.py                # Book / Chapter ORM (无 Paragraph)
├── services/
│   ├── __init__.py
│   ├── parser.py            # TXT 解析与入库
│   └── translator.py        # LLM 调用, 单章 translate_chapter()
├── templates/
│   ├── base.html            # 引入 Tailwind + Alpine.js CDN
│   ├── index.html           # 书架 + 上传表单
│   └── reader.html          # 三种阅读模式 + 每章状态徽章 + 下一章自动触发
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
| `books` | `id`, `title`, `file_path`, `total_chapters`, `translated_chapters`, `created_at` | 书籍 |
| `chapters` | `id`, `book_id`, `title`, `chapter_index`, `content`, `translated_content`, `status` | 章节 |

`chapters.status`: `0` = 待翻译 · `1` = 翻译中 · `2` = 完成 · `-1` = 失败 · `3` = 章节被判定为过长 (单章 > `CHAPTER_MAX_CHARS`)

---

## 路由

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET`  | `/` | 首页 (书架 + 上传) |
| `POST` | `/upload` | 接收 TXT → 解析入库 → 303 跳转阅读器 (**不自动翻译**) |
| `GET`  | `/read/{book_id}` | 渲染阅读器 (按章列表, 包含 next_chapter_id) |
| `GET`  | `/api/chapter/{chapter_id}` | 返回章节完整数据 (content + translated_content) |
| `POST` | `/translate/stream/{chapter_id}` | SSE 流式翻译, 前端通过 `fetch` + `ReadableStream` 读取 |
| `GET`  | `/api/chapter-status/{book_id}` | 返回每章状态 (备用同步机制) |
| `GET`  | `/docs` | FastAPI 自动生成的 Swagger UI |

### `POST /translate/stream/{chapter_id}` SSE 事件格式

每个事件以 `data: ` 前缀, JSON 格式:

| type | 含义 |
| --- | --- |
| `status` | 状态变更 (`started` / `too_long`) |
| `progress` | 进度更新 (每 20 个 token) |
| `chunk` | LLM 输出的文本片段 |
| `done` | 翻译完成, `text` 字段为完整双语译文 |
| `error` | 翻译失败, `reason` 字段为原因 |

### `GET /api/chapter/{chapter_id}` 返回结构

```json
{
  "id": 1,
  "title": "第一章",
  "content": "原始中文内容...",
  "translated_content": "English paragraph 1\n\nEnglish paragraph 2...",
  "status": "done",
  "char_count": 1234,
  "next_chapter_id": 2
}
```

---

## 工作原理

### 解析流程 (`services/parser.py`)

1. 按 `\n` 切行
2. 命中 `第X章/回/节/卷/篇 | Chapter X | CHAPTER X` 的行, 作为新章节标题
3. 前文作为独立"序章"章节
4. 全无章节标记时, 整本归为"全文"章节
5. 每章 content 保留原始文本 (含换行)

### 翻译流程 (`services/translator.py`)

1. 上传**不自动翻译**, 阅读器加载后停在纯中文模式
2. 切换到英文/双语后, 自动触发流式翻译 (或点击"翻译本章"按钮)
3. 后端: `POST /translate/stream/{id}` → 校验 → `translate_chapter_stream()` 流式调用 LLM
4. LLM 响应通过 SSE 逐 token 推送到前端, 前端实时显示翻译进度
5. 流结束后, 存入 `Chapter.translated_content`, 标记 `status=2`
6. 前端收到 `done` 事件后重新加载章节, 渲染英文译文

### 阅读器 (`templates/reader.html`)

- 顶层 `x-data="reader"` Alpine.js 组件
- 章节懒加载: 导航时才 `fetch('/api/chapter/{id}')`
- 两种模式渲染:
  - **中文**: 直接渲染 `content`
  - **英文**: 渲染 `translated_content` (英文段落), 点击任意段落显示对应原文 (淡色字体)
- 翻译流式显示: SSE 连接, 逐 token 累积显示, 完成后自动切换到正式渲染
- 状态徽章: 5 种 (未翻译 / 翻译中 / 已翻译 / 失败 / 超长) 各配 Tailwind 配色

---

## Prompt 模板 (按章翻译)

```
SYSTEM:
你是一位资深中英文学翻译。
将用户给定的整章中文翻译为英文, 保留原文语气、风格、文学性。
保持与原文完全相同的段落数, 段与段之间用一个空行分隔。
只输出英文译文, 段间空行分隔, 严禁任何解释、标题、注释或额外内容。

USER:
{上一章英译, 超 30K 字符时自动截取头尾各半}

Chapter: {章节标题}
请将以下整章中文翻译为英文:
{当前章整章原文}
```

---

## 常见问题

**没有 API Key 能用吗?**
可以。上传 / 解析 / 中文阅读完全正常, 切换到英文时会显示"翻译中…", 翻译需要配置 API Key。

**想换 OpenAI / 智谱 / 自建网关?**
`.env` 改 `LLM_API_KEY`、`LLM_API_BASE`、`LLM_MODEL` 即可。DeepSeek 特有的 `extra_body` 参数会自动跳过, 无需手动处理。也兼容旧变量名 `DEEPSEEK_*`。

**端口 8000 被占用?**
改 `main.py` 末尾 `uvicorn.run(..., port=8000)` 即可。

**单章太长被拒绝了怎么办?**
`status=3` 表示拒绝翻译。前端徽章会提示"本章过长"。后续可手动把该章在源 TXT 里拆成两章 (例如在中间插入一个新章节标题), 然后重新上传。

**翻译失败怎么办?**
章节 `status=-1` 时, 前端显示"翻译失败"。可点击"翻译本章"按钮重新触发。

**支持 EPUB / MOBI / PDF 吗?**
目前只支持纯 TXT。EbookLib (epub) 容易接入; PDF 需要 pdfplumber + 排版还原, 工作量较大, 见后续路线。

---

## 路线图

- [ ] 章节级翻译断点续传, 中途重启不丢工
- [ ] EPUB / MOBI 解析
- [ ] 翻译术语表 (glossary) 注入 Prompt
- [ ] 用户账号与多设备同步

---

## 许可

MIT
