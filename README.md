# PaperReader 论文阅读器

本地优先的 AI 论文阅读工作台：管理论文 PDF，调用 LLM（任意 OpenAI 兼容 API）通读论文生成结构化中文 Summary，并围绕论文进行**多轮对话**与**对话式笔记整理**。

- **FastAPI 后端 + React 前端**，单条命令启动，数据全部保存在本机
- **已读的唯一标准**：`papers/<stem>.pdf` 在 `summaries/` 下存在对应的 `<stem>.md`
- 支持 CLI 与 Web 界面双形态

## 功能特性

| 功能 | 说明 |
|---|---|
| 📥 URL 一键导入 | 粘贴 arXiv / PDF 链接自动下载入库（`/abs/` 链接自动转 PDF 直链；重名 409、非 PDF 422） |
| 📄 对照阅读 | 左侧内嵌 PDF 原文、右侧分章节彩色卡片式 Summary |
| 🤖 AI Summary | 摘要 / 核心技术方案 / 实验效果 / 结论 / **Agent 思考**；一次性生成（非流式），解析失败自动修复，杜绝空章节 |
| 💬 流式对话 | 基于论文全文的多轮问答，回答打字机式实时显示；未读论文直接提问会先生成 Summary 再回答，回答后给出可点击的关联追问 |
| 🗂 对话式笔记 | 回答一键存为摘录 → AI 将「已有笔记 + 新摘录」融合整理成一篇（章节注明来源）；可整篇重新整理；整理完成后自动清空摘录与手写笔记卡片 |
| ✎ 手写笔记 | 卡片式手写想法：多条、可编辑、可删除；草稿输入即自动暂存，点「保存」生成卡片 |
| ⬇ 一键导出 | Summary / 全部笔记分别导出为 `.md` 文件 |
| 🔍 全文搜索 | 输入 2 字以上自动跨所有 Summary 全文检索，命中关键词高亮 |
| ⭐ 收藏 / 已读 | 全部 / 收藏 / 已读 / 未读 筛选，收藏置顶 |
| 🌙 深色模式 | 浅色 / 深色主题一键切换 |

> 思考型模型的「原始推理过程」只写入 `summaries/*.md` 存档，Web 界面与对话均不展示（API 返回前已剥离）。

## 快速开始

### 1. 后端（Python ≥ 3.11）

```bash
cd backend
uv sync                 # 方式 A：uv 创建虚拟环境并安装依赖
# 或 pip install -e .   # 方式 B：pip 可编辑安装
uv run pytest           # 可选：运行测试
```

### 2. 配置 LLM API（任意 OpenAI 兼容服务）

复制 `paperreader.toml.example` 为项目根目录的 `paperreader.toml`（**已被 .gitignore 忽略，不会提交**），填入你的 Key：

```toml
api_base = "https://open.bigmodel.cn/api/paas/v4"   # 或其他 OpenAI 兼容端点
api_key  = "sk-..."
model    = "glm-4.5"        # 建议思考型模型，可捕获推理过程
proxy    = ""               # 可选：出站代理，如 "http://127.0.0.1:10808"
```

也可用环境变量：`PAPERREADER_API_KEY` / `PAPERREADER_API_BASE` / `PAPERREADER_MODEL`（网关特殊请求头可用 `PAPERREADER_EXTRA_HEADERS`，JSON 格式）。
配置完成后用 `uv run paper config` 检查（Key 已脱敏显示）。

### 3. 前端

```bash
cd frontend
npm install
npm run build        # 产物输出到 frontend/dist，paper serve 会直接托管
```

### 4. 启动

```bash
cd backend
uv run paper serve            # 默认 http://127.0.0.1:8765
uv run paper serve --port 9000
```

打开浏览器访问即可。开发模式：`cd frontend && npm run dev`（http://localhost:5173，/api 自动代理到 8765）。

### CLI 速览

```bash
uv run paper list                    # 列出论文及已读/未读状态
uv run paper list --unread           # 只看未读
uv run paper summarize 2609.16564v1  # 生成 summary（--force 重新生成）
uv run paper show 2609.16564v1       # 查看 summary
uv run paper serve                   # 启动 API + Web 服务
```

## 项目结构

```
papers/                    # 论文库（把 PDF 放这里，支持子目录；PDF 不入库）
summaries/                 # Summary 库（.md 与 PDF 同名对应；不入库）
backend/                   # Python：提取 / 生成 / 对话 / CLI / API 服务
├── paper_reader/
│   ├── config.py          # 配置（env > 项目 toml > 用户 toml > 默认值）
│   ├── library.py         # 扫描论文库，判定已读/未读
│   ├── extract.py         # PDF 文本提取（pypdf，带【第 N 页】分页标记 + 缓存）
│   ├── summarizer.py      # LLM 调用 + Agent 思考捕获 + Markdown 渲染
│   ├── chat.py            # 论文对话 / 摘录整理（含流式与提示词模板）
│   ├── cli.py             # CLI 入口（typer）
│   └── server.py          # FastAPI REST/SSE API（供 Web 使用）
└── tests/
frontend/                  # TypeScript + React（Vite）
└── src/
    ├── App.tsx            # 布局 / 侧栏 / 对照阅读 / 导入 / 搜索 / 主题
    ├── ChatPanel.tsx      # 流式对话（先摘要后回答、关联追问、状态按论文跟踪）
    ├── NotesPanel.tsx     # 笔记：已整理 / 摘录 / 手写 / 导出
    ├── Markdown.tsx       # 通用 Markdown 渲染（对话 / Summary / 笔记共用）
    ├── api.ts             # REST + SSE 客户端
    └── summary.ts         # Summary 解析 / 下载工具
paperreader.toml.example   # 配置模板（复制为 paperreader.toml 后填写）
```

## Summary 格式

`summaries/<stem>.md`，含 YAML frontmatter（title/authors/institutions/venue/year/model 等）与正文章节：

```
# 论文标题
> **作者**：…  **机构**：…  **来源**：…
## 摘要
## 核心技术方案
## 实验效果
## 结论
## Agent 思考          # 模型的批判性评价
### 原始推理过程        # 思考型模型的 reasoning_content（仅存档，界面/API 不展示）
```

## 配置项

| 配置 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| api_base | PAPERREADER_API_BASE | 智谱开放平台 | OpenAI 兼容端点 |
| api_key | PAPERREADER_API_KEY | — | API 密钥 |
| model | PAPERREADER_MODEL | glm-4.5 | 建议思考型模型；须与所选网关的模型 ID 一致 |
| proxy | — | 空 | 出站 HTTP 代理（如 `http://127.0.0.1:10808`） |
| extra_headers | PAPERREADER_EXTRA_HEADERS（JSON） | 空 | 附加请求头；OpenCode 网关会话头按域名自动附加，其余网关无需特判 |
| max_chars | PAPERREADER_MAX_CHARS | 200000 | 送入 LLM 的最大字符数（超出保留首 70% + 尾 30%） |
| timeout | PAPERREADER_TIMEOUT | 900 | API 超时（秒） |
| papers_dir | PAPERREADER_PAPERS_DIR | `<项目根>/papers` | 论文目录 |
| summaries_dir | PAPERREADER_SUMMARIES_DIR | `<项目根>/summaries` | Summary 目录 |

项目根的探测：从运行目录向上查找 `paperreader.toml` 或 `papers/` 目录，CLI 在任意子目录运行均可。

## REST / SSE API（`paper serve`，默认 127.0.0.1:8765）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/papers` | 论文列表 + 已读/未读统计 |
| GET | `/api/papers/{stem}/summary` | 获取 summary markdown（已剥离推理过程） |
| POST | `/api/papers/{stem}/summarize?force=` | 生成/重新生成 summary（非流式，解析失败自动修复一轮） |
| POST | `/api/papers/{stem}/summarize/stream` | **已废弃**（410）：Summary 改为一次性生成 |
| DELETE | `/api/papers/{stem}/summary` | 删除 summary（标记未读） |
| GET | `/api/papers/{stem}/text` | 提取的 PDF 全文（含分页标记） |
| GET | `/api/papers/{stem}/pdf` | PDF 原文件（`Content-Disposition: inline`） |
| POST | `/api/papers/{stem}/chat` | 论文问答（一次性返回） |
| POST | `/api/papers/{stem}/chat/stream` | SSE 流式问答 |
| POST | `/api/papers/{stem}/related-questions` | 关联追问（基于最近一轮问答生成 3 个后续问题，失败返回空） |
| GET | `/api/meta` | 全部论文元信息（星标/笔记） |
| PUT | `/api/papers/{stem}/meta` | 更新收藏 / 手写笔记 / 摘录 / 整理笔记 |
| POST | `/api/papers/{stem}/digest-clips` | 摘录整理（`prior` 传已有笔记时做融合整理） |
| POST | `/api/papers/{stem}/redigest` | 重新整理某篇笔记 |
| POST | `/api/papers/import` | URL 导入论文（支持 arXiv 链接自动转换） |
| GET | `/api/search?q=` | 跨 Summary 全文搜索 |

`stem` 为相对 papers/ 去掉 `.pdf` 的路径（如 `2024/attention`），URL 中需编码。

## 安全与隐私

- **API Key 只存在本地**：`paperreader.toml`（或环境变量）已被 `.gitignore` 忽略，仓库中仅含占位符模板 `paperreader.toml.example`
- **个人数据不入库**：论文 PDF（`papers/`）、生成的 Summary（`summaries/`）、星标与笔记（`paperreader_meta.json`）均在 `.gitignore` 中
- 所有请求默认只发往本机（`127.0.0.1`）；出站仅访问你配置的 LLM API

## 技术说明

- PDF → 文本用 pypdf 提取（带分页标记 + 进程内缓存），不把 PDF 直接发给模型；扫描版/图片型 PDF 会给出明确提示（暂不支持 OCR）
- Summary 由模型输出 JSON 解析（容忍代码块包裹与前后多余文本），再渲染为固定模板 Markdown，结构稳定
- 对话上下文 = Summary + PDF 全文（超长自动保留首尾），历史消息只保留最近 20 轮
- 流式基于 SSE（`fetch` + `ReadableStream`），前端按论文跟踪进行中状态，切页不丢
- 已读/未读完全由文件系统状态决定，删除 `.md` 即回到未读
