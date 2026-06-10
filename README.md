# Vibe Reading — 沉浸式英语学习阅读

上传中文 TXT 电子书，自动识别章节，逐章调用 LLM 翻译为英文，提供中/英双语阅读模式。

零前端构建（无 npm/Vite），`uv run main.py` 一行启动。

---

## 快速开始

```bash
uv sync              # 安装依赖
uv run main.py       # 启动 → http://127.0.0.1:8000
```

首次启动自动创建 `vibe_reading.db`（SQLite）和 `uploads/` 目录。

### 配置 LLM

在阅读页工具栏 → 设置，填入 API Key / Base / Model 后保存。支持任意 OpenAI 兼容接口：

| 提供方 | API Base | Model |
|--------|----------|-------|
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` |

---

## 特性

- **TXT 解析** — 正则识别「第 X 章/回/节/卷/篇」与「Chapter X」，自动分割章节
- **流式翻译** — 英文模式下自动触发，LLM 响应逐 token SSE 推送，实时可见
- **双语阅读** — 中文模式只显示原文；英文模式显示译文，点击段落显示对应原文
- **阅读进度** — 服务端保存阅读位置，首页显示「继续阅读」
- **重新翻译** — ↻ 按钮重置章节状态后重新翻译
- **两套主题** — 导航栏切换「原木」（暖棕）/「青简」（墨绿），localStorage 持久化
- **滚动沉浸** — 下滑自动隐藏导航栏，上滑恢复

---

## 项目结构

```
vibe_reading/
├── main.py               # FastAPI 入口 + 路由
├── database.py            # 异步 SQLAlchemy 引擎/Session
├── models.py              # Book / Chapter ORM
├── services/
│   ├── parser.py          # TXT 解析入库
│   ├── translator.py      # LLM 流式翻译
│   └── settings.py        # settings.json 读写
├── templates/             # Jinja2 模板
├── static/style.css       # 设计系统 + 双主题 CSS 变量
├── pyproject.toml
└── uv.lock                # 锁定依赖（入库）
```

---

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/upload` | 上传 TXT，解析入库（不自动翻译） |
| `POST` | `/delete/{book_id}` | 删除书籍 |
| `GET` | `/read/{book_id}` | 阅读页 |
| `GET` | `/api/chapter/{id}` | 获取章节数据 |
| `POST` | `/translate/stream/{id}` | SSE 流式翻译 |
| `POST` | `/api/reset-chapter/{id}` | 重置章节状态为 pending |
| `POST` | `/api/reading-progress/{book_id}` | 保存阅读进度 |
| `GET` | `/api/chapter-status/{book_id}` | 获取所有章节状态 |
| `GET/POST` | `/api/settings` | 读写 LLM 设置 |
| `POST` | `/api/settings/test-llm` | 测试 LLM 连接 |

---

## 数据模型

- **books**: `id`, `title`, `file_path`, `total_chapters`, `translated_chapters`, `last_read_chapter_id`
- **chapters**: `id`, `book_id`, `title`, `chapter_index`, `content`, `translated_content`, `status`

`status`: `0`=待翻译 · `1`=翻译中 · `2`=完成 · `-1`=失败 · `3`=过长

---

## 许可

MIT
