# Vibe Reading — 中英双语电子书阅读器

一个本地部署的单体 Python Web 应用: 上传中文 TXT 电子书, 自动识别章节, 逐**章**调用 DeepSeek 翻译为英文, 提供"中 / 英 / 双语"三种阅读模式。

零前端构建 (无 npm / Vite / Webpack), 一行命令启动。

---

## 特性

- **TXT 解析**: 正则识别"第 X 章 / 回 / 节 / 卷 / 篇"与"Chapter X", 按非空行拆段
- **按章翻译 (Lazy)**: 上传后**默认停在纯中文模式**; 切换到英文/双语时, 通过每章独立的"翻译本章"按钮或点击"下一章"链接, 单章触发一次 DeepSeek 调用
  - 单章 = 一次 API 调用, 段间空行分隔保持段落结构
  - 单章 > 20K 字符直接拒绝翻译 (返回 `too_long`, 不降级)
  - 上一章英译作为**语境** (Prompt 上下文), 超 30K 字符自动截取头尾各半
  - 端点**幂等**: 同一章处于 `in_progress` / `done` / `empty` / `too_long` 时直接返回, 不重复入队
- **三种阅读模式** (Alpine.js):
  - **纯中文**: 仅渲染中文段落
  - **纯英文**: 点击英文段落, `x-transition` 平滑展开对应中文 (手风琴)
  - **中英双语**: 中文 + 英文相邻渲染, 形成"上中下英"布局
- **每章实时状态**: 3 秒轮询, 每章独立徽章 (未翻译 / 翻译中 / 已翻译 / 失败 / 部分 / 超长)
- **零构建**: 模板 + Tailwind / Alpine CDN, 改完直接刷新浏览器

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | FastAPI · Jinja2 · SQLAlchemy 2 (async) · SQLite (aiosqlite) · openai (官方 SDK) · python-dotenv |
| 前端 | 原生 HTML + Jinja2 模板 · TailwindCSS (CDN) · Alpine.js (CDN) |
| LLM | DeepSeek API (走 OpenAI Chat Completions 格式, 关闭思考模式) |

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
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
CHAPTER_MAX_CHARS=20000
```

> DeepSeek 官方推荐 `https://api.deepseek.com` (无 `/v1`), `https://api.deepseek.com/v1` 也可。

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
│   └── translator.py        # DeepSeek 调用, 单章 translate_chapter()
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
| `books` | `id`, `title`, `file_path`, `total_paragraphs`, `translated_count`, `created_at` | 书籍 |
| `chapters` | `id`, `book_id`, `title`, `chapter_index` | 章节 |
| `paragraphs` | `id`, `chapter_id`, `paragraph_index`, `original_text`, `translated_text`, `status` | 段落 |

`paragraphs.status`: `0` = 待翻译 · `1` = 翻译中 · `2` = 完成 · `-1` = 失败 · `3` = 章节被判定为过长 (单章 > `CHAPTER_MAX_CHARS`, 标在所有段落上)

---

## 路由

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET`  | `/` | 首页 (书架 + 上传) |
| `POST` | `/upload` | 接收 TXT → 解析入库 → 303 跳转阅读器 (**不自动翻译**) |
| `GET`  | `/read/{book_id}` | 渲染阅读器 (按章树状结构, 包含 next_chapter_id) |
| `POST` | `/translate/chapter/{chapter_id}` | 触发单章翻译, 幂等 |
| `GET`  | `/api/chapter-status/{book_id}` | 前端 3 秒轮询, 返回每章聚合状态 |
| `GET`  | `/docs` | FastAPI 自动生成的 Swagger UI |

### `POST /translate/chapter/{chapter_id}` 返回值

| 状态码 | body | 含义 |
| --- | --- | --- |
| 200 | `{status: "started", char_count: N}` | 启动后台任务成功 |
| 200 | `{status: "in_progress"}` | 已有段落处于翻译中 (幂等) |
| 200 | `{status: "done"}` | 整章已全部完成 (幂等) |
| 200 | `{status: "empty"}` | 章节无段落 (幂等) |
| 200 | `{status: "too_long", char_count: N}` | 单章超过 20K 字符, **拒绝翻译**, 所有段落标 3 |
| 404 | `{detail: "章节不存在"}` | chapter_id 无效 |

### `GET /api/chapter-status/{book_id}` 返回结构

```json
{
  "book_id": 1,
  "chapters": [
    {
      "id": 1,
      "title": "第一章",
      "status": "pending",
      "char_count": 36,
      "paragraph_count": 2,
      "translated_count": 0
    }
  ]
}
```

每章 `status` 聚合规则: `too_long` (sum chars > `CHAPTER_MAX_CHARS`) · `in_progress` (任意 paragraph.status==1) · `done` (全 2) · `partial` (部分 2 部分 -1) · `failed` (全 -1) · `pending` (其余)

---

## 工作原理

### 解析流程 (`services/parser.py`)

1. 按 `\n` 切行, 跳过空行
2. 命中 `第X章/回/节/卷/篇 | Chapter X | CHAPTER X` 的行, 作为新章节标题 (取匹配到的整段作 title, 避免吞掉第一段)
3. 其余非空行作为段落追加到当前章节
4. 全无章节标记时, 整本归为"全文"章节

### 翻译流程 (`services/translator.py`)

1. 上传**不自动翻译**, 阅读器加载后停在纯中文模式
2. 切换到英文/双语后, 用户点击"翻译本章"或点击"下一章 ↓"链接触发
3. 后端: `POST /translate/chapter/{id}` → 校验 (in_progress / done / empty / too_long 各自早退) → 通过 `BackgroundTasks.add_task` 启动 `translate_chapter()`
4. `translate_chapter()` 拉该章所有段落 + 上一章的英译 (作为语境) → 拼成一条 Prompt → `openai.AsyncOpenAI` 调 DeepSeek (非思考模式, `extra_body={"thinking": {"type": "disabled"}}`) → 按空行拆回复 → UPDATE 段落 (status=2, translated_text=...)
5. 段落数对不上时补空字符串 / 截断, 落库时打 WARN 日志

### 阅读器 (`templates/reader.html`)

- 顶层 `x-data="{ bookId, mode: 'zh', expandedId, chapterStatus, ... }"`
- 章节 DOM 结构: `<article id="chapter-N">` 内含 `<header>` (徽章 + 翻译本章按钮) + 段落列表 + 底部"下一章 ↓"链接 (最后一章不渲染)
- 状态徽章: 6 种 (未翻译 / 翻译中 / 已翻译 / 失败 / 部分 / 超长) 各配 Tailwind 配色
- `setInterval` 每 3 秒拉 `/api/chapter-status`, merge 到 `chapterStatus` map, Alpine 响应式更新徽章/按钮
- "下一章"链接的 `@click` 触发 `maybeTranslateNext(id)`: 仅在 en/both 模式 + 章节未翻译 + 未超长时, 自动 `POST /translate/chapter/{id}`

---

## Prompt 模板 (按章翻译)

```
SYSTEM:
你是一位资深中英双语文学翻译。
- 翻译必须自然流畅、地道, 保留原文语气、风格和文学性。
- 上一章的英文译文会作为【语境参考】, 请勿重复或改写, 也无需翻译。
- 用户会提供当前章节的整章中文原文, 章节内多个段落以空行分隔。
- 只输出一段英文译文, 段落之间用单个空行分隔, 数量与原文段落数严格一致。
- 不要输出任何解释、注释、标题或额外内容。

USER:
以下为上一章的英文译文 (仅作语境参考, 请勿重复或翻译):
---
{上一章英译, 超 30K 字符时自动截取头尾各半}
---

请翻译以下整章, 保持 {K} 个段落 (用单个空行分隔):
---
{当前章整章原文, 段间空行}
```

---

## 常见问题

**没有 DeepSeek API Key 能用吗?**
可以。上传 / 解析 / 中文阅读完全正常, 切换到英文/双语时会显示"未翻译"占位。手动把译文写进 `paragraphs.translated_text` 也能在英文/双语模式显示。

**想换 OpenAI / 智谱 / 自建网关?**
`.env` 改 `DEEPSEEK_API_BASE` 和 `DEEPSEEK_MODEL` 即可, 代码兼容任何 OpenAI Chat Completions 格式 (SDK 直连)。注意: 非 DeepSeek 提供方通常没有 "thinking" 字段, 删掉 `extra_body` 即可。

**端口 8000 被占用?**
改 `main.py` 末尾 `uvicorn.run(..., port=8000)` 即可。

**单章太长被拒绝了怎么办?**
`status=3` 表示拒绝翻译。前端徽章会提示"本章过长"。后续可手动把该章在源 TXT 里拆成两章 (例如在中间插入一个"第一章(续)"标题), 然后重新上传。

**翻译失败的段落怎么办?**
单段 `status=-1` 时, 章节聚合为 `partial` 或 `failed`。可在 SQLite 里 `UPDATE paragraphs SET status = 0, translated_text = NULL WHERE id = ?` 单独重置该段, 或重新触发"翻译本章"按钮整体重来。

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
