# OfficeAgent

> 面向 Windows 本地桌面的 AI 办公自动化工作台

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.10-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![Tauri](https://img.shields.io/badge/Desktop-Tauri%202-24C8DB.svg?logo=tauri&logoColor=white)](desktop-client/)

OfficeAgent 将自然语言、Office 文件和可配置的多模型能力组合成一套本地优先的办公工作流。它覆盖 Word 文档处理、PPT 演示文稿生成、Excel 数据分析与图表、知识库检索（RAG），并提供任务队列、文件版本管理、质量检查、运行日志和 Windows 桌面应用。

当前代码版本：0.52.0

## 项目定位

OfficeAgent 的核心原则是“本地管理文件，模型连接可配置”。上传文件、生成结果、SQLite 数据库、日志和模型配置默认写入本机应用数据目录；当你配置 OpenAI、Claude、Gemini、豆包、通义、DeepSeek、Agnes、自定义 LLM Provider 或自定义 Image Provider 时，相关提示词、文档文本或多模态内容会按任务需要发送给用户选择的服务商。

因此，项目适合：

- 需要用自然语言处理 Word、PPT、Excel 文件的个人用户；
- 希望在本机统一管理模型、文件、任务和生成结果的桌面应用场景；
- 需要多模型路由、失败转移、审计日志和可验证发布产物的开发团队；
- 想把 Office 自动化能力作为后端服务或桌面应用基础设施进行二次开发的项目。

“本地优先”指数据库、文件、配置和日志默认保存在本机，不等于所有任务数据永不离开设备。使用 OpenAI、DeepSeek 或其他云端/自定义 API 时，完成任务所需的数据可能发送到用户自己选择的 Provider，并受该 Provider 的服务条款和隐私政策约束。

## 核心能力

| 能力 | 主要功能 | 主要模块 |
| --- | --- | --- |
| Word Agent | 文档解析、排版规范化、字体/段落/表格检查、原文保留检查、质量报告 | office_agent/services/、office_agent/quality/ |
| PPT Agent | 从主题、文本或 Word 内容生成 PPTX；支持模板、主题、版式、配图与页数控制；支持视觉检查 | office_agent/ppt_agent/、office_agent/vision_gateway/ |
| Excel Agent | 数据画像、自然语言分析、公式生成、图表生成、模板填充、公式与数据质量检查 | office_agent/excel_agent/ |
| Chat / Agent | 自然语言路由、异步任务、任务取消、上下文续改、revision 链和结果文件 | office_agent/api/router/、office_agent/task_queue/ |
| 知识库 / RAG | 文档解析、分块、语义 embedding、向量索引、检索和知识库刷新 | office_agent/knowledge_base/、office_agent/task_queue/tasks/rag_tasks.py |
| 多模型网关 | 多提供商统一接口、原生/自定义 Provider、模型发现、能力路由、优先级和故障转移 | office_agent/model_gateway/、office_agent/models/ |
| 图片生成网关 | Agnes 与 OpenAI Image Compatible Provider、默认 Provider、URL 和 b64_json 结果 | office_agent/image_generation/ |
| Skills | 可复用的 Agent 工作指令与偏好，支持 CRUD、Markdown 导入、Agent 范围和优先级 | office_agent/skills/、office_agent/api/router/skills.py |
| 文件与任务 | 文件上传、分片上传、版本、回收站、任务状态、取消、反馈、历史记录 | office_agent/api/router/、office_agent/database/ |
| 可观测性 | Prometheus 指标、执行日志、模型调用日志、错误日志、Trace 查询 | office_agent/logging_system/、office_agent/api/main.py |
| Windows 桌面 | Tauri 2 + React 工作台，内嵌后端启动、健康检查、任务轮询、原生文件保存和发布打包 | desktop-client/、desktop/ |

## What's New in 0.52.0

0.52.0 在既有 Word、PPT、Excel、Chat、RAG 和本地数据管理能力上，增加了可配置 Provider、动态模型发现、Skills 与可靠的桌面文件下载。它们是对原有 OfficeAgent 工作流的增强，不取代核心 Agent 能力。

### Custom LLM Provider

除了既有 OpenAI、DeepSeek、Anthropic Claude、豆包、通义千问、Google Gemini 和 Agnes 原生 Provider，用户现在可以配置自己的模型服务：

- Provider Name；
- Protocol；
- Base URL；
- API Key；
- 模型列表与 Default Model；
- Enabled；
- Allow Local Endpoint。

自定义 LLM Provider 支持 **OpenAI Compatible** 与 **Anthropic Compatible** 两种协议。API Key 为空时可保留已有密钥，也可以显式清除；启用状态下不能保存没有 Key 的配置。兼容性取决于第三方 API 是否遵循对应协议。

### Dynamic Model Discovery

配置 Provider 后，可以依次执行：

~~~text
Add Provider
→ Test Connection
→ Detect Models
→ Select Model
→ Save
~~~

模型发现主要请求 Provider 的 `GET /models`。如果第三方 API 不提供该端点、返回格式不同，或当前 Key 无权读取模型列表，可以使用 **Manual Add Model** 填写真实 Model ID，再选择默认模型。

### Custom Image Provider

PPT 生图不再只绑定 Agnes。0.52.0 保留 Agnes，并支持多个 **OpenAI Image Compatible Provider**：

- 配置 Provider Name、Protocol、Base URL、API Key 和 Model；
- Test Connection；
- Detect Models；
- Manual Add Model；
- 选择默认模型；
- Set Default，将该服务设为 PPT 默认图片 Provider；
- 接收图片 URL 或 `b64_json` 两种结果。

兼容图片端点应实现 `/images/generations`。图片 Provider 不可用、超时或限流时，PPT 任务会保留可用的内容与布局，并记录明确的配图状态，而不是把失败静默伪装成成功。

### Native File Download

0.52.0 修复了旧桌面版点击 Download 后可能无反应的问题。Tauri Desktop 会先从后端获取文件内容，再打开 Windows 原生 **Save As** 对话框，由用户选择路径后写入文件；取消保存不会改变原文件，写入失败会给出明确提示。

该流程支持 DOCX、XLSX、PPTX，以及包含中文、空格和括号的文件名。浏览器环境继续使用标准下载 fallback。

### Skills 概览

Skill 是用户可复用的 **Agent 工作指令和工作偏好**，例如商务 PPT 版式、学术报告格式、财务分析规则或团队文档规范。它不是 Shell 插件、Python executable plugin，也不会授予额外的文件、网络、工具或凭据权限。

0.52.0 支持：

- Create、Edit、Delete；
- Enable、Disable；
- UTF-8 Markdown Import；
- `word`、`excel`、`ppt`、`chat` 和 `all` 目标 Agent；
- `priority` 确定应用顺序，数字越小越先应用；
- 单个 Skill 和合并 Skill context 的长度限制。

详细格式与示例见下文[“Skills”](#skills)章节。

## 工作流概览

~~~text
React / Tauri 桌面端
        │  上传文件、发送对话、创建任务、查看结果、原生保存文件
        ▼
FastAPI 本地 API（默认 127.0.0.1:8765）
        │
        ├── 意图路由：Word / PPT / Excel / RAG
        ├── 任务队列：pending → queued → running → success / failed
        ├── 安全边界：认证、权限、输入限制、文件扫描、Prompt 检查
        └── Agent
                ├── Skill Resolver：选择并注入已启用的用户指令
                ├── Model Gateway → Provider Registry
                ├── Image Gateway → Image Provider Registry
                ├── Word / PPT / Excel Service
                ├── 视觉、文档理解、Embedding 与向量检索
                └── 本地 SQLite、文件存储、日志与审计
~~~

## 快速开始

### 环境要求

| 用途 | 要求 |
| --- | --- |
| 后端开发 | Python >=3.10 |
| 桌面前端开发 | Node.js 22、pnpm 11（CI 使用 pnpm 11.7.0） |
| Windows 安装包 | Windows、Python 生产依赖、pnpm、Rust/Cargo |
| 语义 RAG（可选） | sentence-transformers；首次使用本地模型时需要下载模型 |

0.52.0 的稳定 Windows installer 尚未正式发布到 GitHub Releases。Releases 页面将在稳定安装包准备完成后提供安装程序；当前请使用下述源码开发方式，不要把仓库中的调试产物当作正式安装包。

### 1. 安装后端依赖

PowerShell：

~~~powershell
py -3.12 -m venv .venv
./.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
~~~

如果希望使用本地 sentence-transformers embedding：

~~~powershell
python -m pip install -e ".[dev,semantic]"
~~~

### 2. 启动本地 API

~~~powershell
python -m office_agent.api.main
~~~

启动后可访问：

- Swagger UI：<http://127.0.0.1:8765/docs>
- ReDoc：<http://127.0.0.1:8765/redoc>
- 就绪探针：<http://127.0.0.1:8765/ready>
- 详细健康检查：<http://127.0.0.1:8765/api/health>
- Prometheus 指标：<http://127.0.0.1:8765/metrics>

### 3. 启动桌面前端开发服务器

在另一个终端执行：

~~~powershell
pnpm --dir desktop-client install --frozen-lockfile
pnpm --dir desktop-client run dev
~~~

打开 Vite 输出的本地地址，通常为 <http://127.0.0.1:5173>。桌面端包含工作台、Word、PPT、Excel、任务历史、文件管理、Providers、Skills 和设置页面。

如需启动完整 Tauri 桌面开发实例，执行：

~~~powershell
pnpm --dir desktop-client tauri dev
~~~

debug Tauri 会从当前源码自动启动并管理本地 backend；如果 `127.0.0.1:8765` 已有健康的 OfficeAgent backend，则复用该进程。

## 第一次配置模型

启动后进入“模型与生图服务”或“设置”，按用途配置语言模型、生图模型和 Embedding 模型。语言模型原生 Provider 包括：

~~~text
openai       OpenAI
deepseek     DeepSeek（OpenAI 兼容接口）
doubao       豆包 / 火山引擎
qwen         通义千问（OpenAI 兼容接口）
claude       Anthropic Claude
gemini       Google Gemini
agnes        Agnes AI（OpenAI 兼容接口）
custom       自定义 OpenAI / Anthropic Compatible Provider
~~~

也可以通过环境变量提供原生 Provider 的 API Key，例如：

~~~powershell
$env:OPENAI_API_KEY = "your-key"
$env:DEEPSEEK_API_KEY = "your-key"
$env:ANTHROPIC_API_KEY = "your-key"
$env:GEMINI_API_KEY = "your-key"
$env:DOUBAO_API_KEY = "your-key"
$env:DASHSCOPE_API_KEY = "your-key"
$env:AGNES_API_KEY = "your-key"
~~~

不要把真实密钥写入源代码、.env 并提交到仓库，也不要把密钥放进任务指令、Issue 或日志。通过设置页保存的模型 API Key 使用 Fernet 加密后写入本地配置；相关 API 只返回掩码，前端本地持久化设置不会保存 API Key。

### Custom Provider Example

~~~text
Provider Name: My API
Protocol: OpenAI Compatible
Base URL: https://example.com/v1
API Key: ********
~~~

配置流程：

~~~text
Add Provider
→ Test Connection
→ Detect Models
→ Select Model
→ Save
~~~

如果 Detect Models 失败但服务确实遵循所选协议，请使用 **Manual Add Model** 填写 Model ID。Anthropic-compatible 服务使用相同配置流程，但聊天端点和认证头按 Anthropic 协议处理。

### 本地兼容 API

Custom Provider 可以连接用户显式允许的 localhost 或私网 OpenAI/Anthropic-compatible endpoint。必须开启 **Allow Local Endpoint**，并通过 Test Connection 验证实际端点。

该能力不代表对 Ollama、LM Studio 或任一具体产品的完整兼容承诺。链路本地、元数据、未指定、多播和保留地址仍受安全策略限制。

### 图片 Provider 配置

~~~text
Provider Name: My Image API
Protocol: OpenAI Image Compatible
Base URL: https://images.example.com/v1
API Key: ********
Model: my-image-model
~~~

配置后执行：

~~~text
Test Connection
→ Detect Models
→ Select Model（或 Manual Add Model）
→ Save
→ Set Default
→ PPT Agent 使用
~~~

## Skills

Skill 可在 Skills 页面创建、编辑、删除、启用或停用，也可从 Markdown 文件导入。UI/API 使用 `target_agents` 字段；Markdown frontmatter 使用 parser 实际识别的 `agents` 字段。

下面是一份可以直接导入的合法示例：

~~~markdown
---
name: 极简商务PPT
description: 用于商务汇报
agents:
  - ppt
priority: 100
---

# Instructions

- 每页只表达一个核心观点
- 每页不超过 5 个要点
- 优先使用图表
- 避免大段文字
- 最后一页总结 3 个核心结论
~~~

Markdown 导入要求 UTF-8 `.md` 文件、闭合的 frontmatter 和非空 Instructions，文件不能超过 256 KB。当前 parser 识别 `name`、`description`、`agents`、`priority`；目标 Agent 只允许 `word`、`excel`、`ppt`、`chat`、`all`。

UI 创建的 Instructions 最长 24000 字符；运行时单个 Skill 最多注入 6000 字符，全部 Skill context 最多 12000 字符。Skill 的优先级低于系统安全策略和 Agent 核心约束，也不能覆盖输出校验规则。

## 数据目录

所有入口（开发后端、任务 Worker、桌面启动器和 Windows 服务）使用同一套数据目录解析规则：

1. `OFFICE_AGENT_DATA_DIR`：显式指定，优先级最高；
2. frozen/安装版 Windows：`%APPDATA%/OfficeAgent`；
3. 源码开发：`~/.office_agent`。

常见内容包括：

~~~text
<data-root>/
├── db/                    SQLite 数据库
├── uploads/               上传文件
├── outputs/               生成结果
├── logs/                  日志与审计相关数据
├── models.json            加密的语言模型与自定义 Provider 配置
├── image_model.json       加密的图片 Provider 配置
├── embedding_config.json  Embedding 配置
├── master.key             模型密钥加密主密钥
└── key_salt.bin           历史配置兼容所需的盐值（如存在）
~~~

可以使用以下变量调整目录：

~~~powershell
$env:OFFICE_AGENT_DATA_DIR = "D:/OfficeAgentData"
$env:OFFICE_AGENT_UPLOAD_DIR = "D:/OfficeAgentData/uploads"
$env:OFFICE_AGENT_OUTPUT_DIR = "D:/OfficeAgentData/outputs"
$env:OFFICE_AGENT_LOG_DIR = "D:/OfficeAgentData/logs"
~~~

## API 使用示例

### 对话入口

对话接口会根据文字和附件类型自动路由到 Word、PPT 或 Excel Agent：

~~~powershell
curl.exe -X POST http://127.0.0.1:8765/api/chat -H "Content-Type: application/json" -d '{"message":"请分析这份 Excel，找出异常趋势并给出结论"}'
~~~

### 文件上传

~~~powershell
curl.exe -X POST http://127.0.0.1:8765/api/file/upload -F "file=@C:/path/to/report.xlsx"
~~~

上传返回的 `file_id` 可以用于后续对话或异步任务。

### 创建异步任务

~~~powershell
curl.exe -X POST http://127.0.0.1:8765/api/task/create -H "Content-Type: application/json" -d '{"task_type":"excel_analyze","instruction":"分析销售趋势并生成摘要","file_ids":["<file_id>"]}'
~~~

返回 `task_id` 后，通过 `GET /api/task/{task_id}` 查询进度和结果。主要任务类型包括：

| 任务类型 | 用途 |
| --- | --- |
| word_format / word_process | Word 排版或处理 |
| ppt_generate / ppt_process | PPT 生成（ppt_process 为兼容别名） |
| ppt_design | PPT 设计处理 |
| excel_analyze | Excel 数据分析 |
| excel_chart | Excel 图表生成 |
| file_convert / file_process | 文件转换或处理 |
| general | 根据指令自动路由 |
| rag_index / rag_search | 知识库索引或检索 |

## RAG 与 Embedding

RAG 默认使用真正的 semantic embedding，不会静默把哈希词袋当作语义检索，也不会混合不同语义空间的旧向量。

### 桌面版

frozen 桌面构建不打包 torch 和 transformers。请在设置页配置一个 OpenAI-compatible Embedding Provider：

- provider：OpenAI、火山引擎、通义或其他兼容服务；
- base_url：服务地址；
- model：embedding 模型名称；
- api_key：访问密钥。

Embedding 配置写入本地统一数据目录的 `embedding_config.json`，API Key 与其他模型密钥一样加密保存。

### 本地开发版

安装 `.[semantic]` 后，运行时优先使用本地 sentence-transformers，默认模型为 `paraphrase-multilingual-MiniLM-L12-v2`。首次使用可能需要联网下载模型，之后可以离线复用缓存。

如果既没有配置 Embedding Provider，也没有安装 semantic extra，RAG 任务会明确失败并提示配置方式，而不是返回看似成功的空结果。

## 配置参考

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| OFFICE_AGENT_HOST | 127.0.0.1 | API 监听地址 |
| OFFICE_AGENT_PORT | 8765 | API 监听端口 |
| OFFICE_AGENT_DEBUG | false | 调试模式 |
| OFFICE_AGENT_DATA_DIR | 按运行模式决定 | 统一应用数据根目录 |
| OFFICE_AGENT_UPLOAD_DIR | `<data-root>/uploads` | 上传目录 |
| OFFICE_AGENT_OUTPUT_DIR | `<data-root>/outputs` | 生成目录 |
| OFFICE_AGENT_LOG_DIR | `<data-root>/logs` | 日志目录 |
| OFFICE_AGENT_AUTH_ENABLED | false | 是否启用 API 认证 |
| OFFICE_AGENT_API_KEYS | 空 | 逗号分隔的 API Key |
| OFFICE_AGENT_JWT_SECRET | 空 | JWT 密钥，启用时至少 32 字节 |
| OFFICE_AGENT_TRUSTED_PROXIES | 127.0.0.1,::1 | 可信代理地址或 CIDR |
| OFFICE_AGENT_FONT | 自动选择 | 文档/PPT 视觉渲染使用的字体路径 |
| OFFICE_AGENT_EMBEDDING_BACKEND | auto | Embedding 后端选择 |
| OFFICE_AGENT_EMBEDDING_MODEL | 自动选择 | 本地 Embedding 模型名 |

默认 API 只绑定回环地址，认证默认关闭，适合本机桌面模式。如果要监听局域网或其他非回环地址，必须同时设计认证、CORS、可信代理、HTTPS 和文件存储边界；启用认证但没有凭据时，服务会在启动阶段失败，而不是带错误配置运行。

## 安全边界

- API 默认绑定 127.0.0.1，降低桌面应用被局域网直接访问的风险；
- API Key 使用 Fernet 加密落盘，旧版密文仅用于兼容读取并在成功读取后迁移；相关 API 只返回掩码，不返回明文 Key；
- 密码哈希使用 PBKDF2-HMAC-SHA256，支持旧版密码在登录时安全升级；
- Custom Provider URL 执行协议、地址、DNS、重定向和响应大小检查；localhost/私网访问必须显式开启，敏感元数据地址仍被拒绝；
- 请求模型会过滤敏感字段，任务选项拒绝客户端伪造服务端路径、凭据和网络能力；
- 文件上传、下载和任务选项包含 owner、扩展名、大小、路径和生命周期边界，并支持文件隔离、版本和回收站；
- CSV 转换包含 Formula Injection protection；
- Prompt 安全层会对模型控制符、可疑指令和外部/文件内容做分级处理；
- Skill 只能注入有边界的 instruction，不能执行 Shell/Python，不能授权工具或绕过系统安全策略；
- 认证、RBAC、速率限制、审计日志、错误脱敏和请求大小限制属于 API 安全层；
- 默认不把“本地子进程 + 临时目录 + 关键词过滤”宣称为操作系统级沙箱。生产环境在接入具备网络禁用、只读文件系统、CPU/内存配额和独立身份的外部执行器前，应保持危险执行能力关闭。

上述措施用于降低风险，不构成“100% 安全”保证。安全设计记录见 [office_agent/security/README.md](office_agent/security/README.md) 和 [docs/architecture/accepted_design_decisions.md](docs/architecture/accepted_design_decisions.md)。RAG 运行时边界见 [docs/architecture/rag_embedding_runtime.md](docs/architecture/rag_embedding_runtime.md)。

## 测试与质量检查

后端全量测试：

~~~powershell
python -m pytest tests/ -m "not slow" --tb=short
~~~

常用静态检查：

~~~powershell
python -m mypy office_agent
python -m ruff check office_agent/ --select=E,F --ignore=E501
~~~

前端检查：

~~~powershell
pnpm --dir desktop-client run lint
pnpm --dir desktop-client run typecheck
pnpm --dir desktop-client run test
pnpm --dir desktop-client run build
~~~

Tauri/Rust 检查：

~~~powershell
cargo fmt --manifest-path desktop-client/src-tauri/Cargo.toml -- --check
cargo check --manifest-path desktop-client/src-tauri/Cargo.toml --locked
cargo test --manifest-path desktop-client/src-tauri/Cargo.toml --locked
~~~

0.52.0 功能提交的已有验收记录为 backend `2231 passed / 0 failed`、frontend `114 passed / 0 failed`。这些数字对应当时验收的提交与测试范围；当前状态仍以目标提交的 CI 和实际复测结果为准。

## Windows 发布构建

Windows 发布入口是 [desktop/build_windows.bat](desktop/build_windows.bat)：

~~~powershell
python -m pip install -r requirements-production.txt
pnpm --dir desktop-client install --frozen-lockfile
.\desktop\build_windows.bat
~~~

发布脚本会：

1. 检查 Python、PyInstaller、pnpm 和 Cargo；
2. 要求已跟踪工作区干净，并记录当前 Git HEAD；
3. 用 PyInstaller 构建 `dist/OfficeAgent/`；
4. 生成并校验包含源码 SHA、文件大小和 SHA-256 的完整 release manifest；
5. 通过 Tauri 构建 Windows 安装包，并在构建前后执行来源守卫。

如果 manifest 缺失、源码 SHA 不一致、文件被篡改、出现未记录文件或构建输入被修改，发布链会以非零状态终止。该流程是 Windows 发布校验链，不等同于代码签名、SmartScreen 或真实 Office GUI 打开/编辑/保存验收。

本节保留源码发布链说明，但 0.52.0 installer 尚未正式发布；本轮也不构建 installer、创建 tag 或 GitHub Release。

## 项目结构

~~~text
OfficeAgent/
├── office_agent/                 # Python 后端与领域能力
│   ├── api/                      # FastAPI、路由、中间件、OpenAPI
│   ├── database/                 # SQLite、SQLAlchemy、Alembic、Repository
│   ├── model_gateway/            # 文本模型客户端、Provider Registry、路由与故障转移
│   ├── image_generation/         # Agnes 与自定义 Image Provider 统一网关
│   ├── skills/                   # Markdown parser、Skill Resolver 与 prompt 注入
│   ├── vision_gateway/           # 图片、PDF、PPT 与扫描文档理解
│   ├── services/                 # Word 等通用文档服务
│   ├── ppt_agent/                # PPT 编排、生成与质量检查
│   ├── excel_agent/              # Excel 分析、公式、图表与模板
│   ├── knowledge_base/           # 解析、分块、Embedding、向量检索
│   ├── task_queue/               # 本地 Worker、调度器和任务实现
│   ├── security/                 # 认证、RBAC、Prompt、文件、Provider 与审计边界
│   ├── quality/                  # 文档与视觉质量检查
│   └── quality_scoring/          # Word/PPT/Excel 质量评分
├── desktop-client/               # React 19 + Vite + Tauri 2
│   └── src-tauri/                # Rust 桌面壳和后端生命周期管理
├── desktop/                      # Windows 启动器、服务和发布脚本
├── tests/                        # 后端单元、集成、回归和发布链测试
├── docs/architecture/            # 架构决策与运行时说明
├── pyproject.toml                # Python 包、开发依赖和 mypy 配置
└── requirements-production.txt   # Windows 发布依赖
~~~

## Known Limitations

- 极端或内容密集的 PPT 长文本布局仍可能需要人工调整；
- 大表格 continuation 和复杂跨页结构仍有限制；
- 图片生成取决于用户配置的 Provider、模型、额度与网络；
- Skills 目前是 instruction-based 能力，不是 executable plugin；
- Custom API 的兼容性取决于第三方是否遵循所选协议；
- `.doc` 和 `.xls` 旧格式不在当前主处理链内，请先另存为 `.docx` 或 `.xlsx`；
- 未安装 LibreOffice 时，部分文档/PPT 视觉检查会降级为结构或文本检查。

## Roadmap

- 更丰富、可分享的 Skill ecosystem；
- 更多经过验证的兼容 Provider 与 Provider templates；
- 改进 PPT 复杂内容布局和长表格续页；
- 完善 Windows signed installer 与分发体验。

Roadmap 表示方向，不承诺具体发布日期。

## 开发约定

- 任何模型密钥、用户文件、数据库和构建产物都不应提交到 Git；
- 修改 API、任务映射、配置来源或数据模型时，同时补充对应测试；
- 处理外部输入时保持显式失败、错误脱敏和边界校验，不要用“看起来成功”的空结果掩盖失败；
- 发布前使用统一的 `desktop/build_windows.bat`，不要绕过 SHA manifest 和来源守卫；
- 涉及安全能力时，区分应用层策略和真正的 OS/容器隔离，不夸大防护强度。

## 许可证

本项目使用 [MIT License](LICENSE)。
