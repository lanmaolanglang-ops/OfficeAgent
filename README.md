# OfficeAgent

> 面向 Windows 本地桌面的 AI 办公自动化工作台

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.10-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![Tauri](https://img.shields.io/badge/Desktop-Tauri%202-24C8DB.svg?logo=tauri&logoColor=white)](desktop-client/)

OfficeAgent 将自然语言、Office 文件和可配置的多模型能力组合成一套本地优先的办公工作流。它覆盖 Word 文档处理、PPT 演示文稿生成、Excel 数据分析与图表、知识库检索（RAG），并提供任务队列、文件版本管理、质量检查、运行日志和 Windows 桌面应用。

当前代码版本：0.51.4

## 项目定位

OfficeAgent 的核心原则是“本地管理文件，模型连接可配置”。上传文件、生成结果、SQLite 数据库、日志和模型配置默认写入本机应用数据目录；当你配置 OpenAI、Claude、Gemini、豆包、通义、DeepSeek、Agnes 或其他兼容服务时，相关提示词、文档文本或多模态内容会按任务需要发送给对应服务商。

因此，项目适合：

- 需要用自然语言处理 Word、PPT、Excel 文件的个人用户；
- 希望在本机统一管理模型、文件、任务和生成结果的桌面应用场景；
- 需要多模型路由、失败转移、审计日志和可验证发布产物的开发团队；
- 想把 Office 自动化能力作为后端服务或桌面应用基础设施进行二次开发的项目。

## 核心能力

| 能力 | 主要功能 | 主要模块 |
| --- | --- | --- |
| Word Agent | 文档解析、排版规范化、字体/段落/表格检查、原文保留检查、质量报告 | office_agent/services/、office_agent/quality/ |
| PPT Agent | 从主题、文本或 Word 内容生成 PPTX；支持模板、主题、版式、配图与页数控制；支持视觉检查 | office_agent/ppt_agent/、office_agent/vision_gateway/ |
| Excel Agent | 数据画像、自然语言分析、公式生成、图表生成、模板填充、公式与数据质量检查 | office_agent/excel_agent/ |
| 知识库 / RAG | 文档解析、分块、语义 embedding、向量索引、检索和知识库刷新 | office_agent/knowledge_base/、office_agent/task_queue/tasks/rag_tasks.py |
| 多模型网关 | 多提供商统一接口、任务能力路由、优先级和故障转移、视觉/文档能力标记 | office_agent/model_gateway/、office_agent/models/ |
| 文件与任务 | 文件上传、分片上传、版本、回收站、任务状态、取消、反馈、历史记录 | office_agent/api/router/、office_agent/database/ |
| 可观测性 | Prometheus 指标、执行日志、模型调用日志、错误日志、Trace 查询 | office_agent/logging_system/、office_agent/api/main.py |
| Windows 桌面 | Tauri 2 + React 工作台，内嵌后端启动、健康检查、任务轮询和发布打包 | desktop-client/、desktop/ |

## 工作流概览

~~~
React / Tauri 桌面端
        │  上传文件、发送对话、创建任务、查看结果
        ▼
FastAPI 本地 API（默认 127.0.0.1:8765）
        │
        ├── 意图路由：Word / PPT / Excel / RAG
        ├── 任务队列：pending → queued → running → success / failed
        ├── 安全边界：认证、权限、输入限制、文件扫描、Prompt 检查
        └── 模型网关：模型选择、能力匹配、优先级、失败转移
                │
                ├── Word / PPT / Excel Service
                ├── 视觉与文档理解
                ├── Embedding 与向量检索
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

打开 Vite 输出的本地地址，通常为 <http://127.0.0.1:5173>。桌面端包含工作台、Word、PPT、Excel、任务历史、文件管理和设置页面。

## 第一次配置模型

启动后进入“设置”，按用途配置语言模型、生图模型和 Embedding 模型。语言模型提供商包括：

~~~text
openai       OpenAI
deepseek     DeepSeek（OpenAI 兼容接口）
doubao       豆包 / 火山引擎
qwen         通义千问（OpenAI 兼容接口）
claude       Anthropic Claude
gemini       Google Gemini
agnes        Agnes AI（OpenAI 兼容接口）
custom       自定义 OpenAI 兼容服务
~~~

也可以通过环境变量提供 API Key，例如：

~~~powershell
$env:OPENAI_API_KEY = "your-key"
$env:DEEPSEEK_API_KEY = "your-key"
$env:ANTHROPIC_API_KEY = "your-key"
$env:GEMINI_API_KEY = "your-key"
$env:DOUBAO_API_KEY = "your-key"
$env:DASHSCOPE_API_KEY = "your-key"
$env:AGNES_API_KEY = "your-key"
~~~

不要把真实密钥写入源代码、.env 并提交到仓库，也不要把密钥放进任务指令、Issue 或日志。通过设置页保存的模型 API Key 使用 Fernet 加密后写入本地配置；前端本地持久化设置不会保存 API Key。

## 数据目录

所有入口（开发后端、任务 Worker、桌面启动器和 Windows 服务）使用同一套数据目录解析规则：

1. OFFICE_AGENT_DATA_DIR：显式指定，优先级最高；
2. frozen/安装版 Windows：%APPDATA%/OfficeAgent；
3. 源码开发：~/.office_agent。

常见内容包括：

~~~text
<data-root>/
├── db/                    SQLite 数据库
├── uploads/               上传文件
├── outputs/               生成结果
├── logs/                  日志与审计相关数据
├── models.json            加密的模型连接配置
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

上传返回的 file_id 可以用于后续对话或异步任务。

### 创建异步任务

~~~powershell
curl.exe -X POST http://127.0.0.1:8765/api/task/create -H "Content-Type: application/json" -d '{"task_type":"excel_analyze","instruction":"分析销售趋势并生成摘要","file_ids":["<file_id>"]}'
~~~

返回 task_id 后，通过 GET /api/task/{task_id} 查询进度和结果。主要任务类型包括：

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

Embedding 配置写入本地统一数据目录的 embedding_config.json，API Key 与其他模型密钥一样加密保存。

### 本地开发版

安装 .[semantic] 后，运行时优先使用本地 sentence-transformers，默认模型为 paraphrase-multilingual-MiniLM-L12-v2。首次使用可能需要联网下载模型，之后可以离线复用缓存。

如果既没有配置 Embedding Provider，也没有安装 semantic extra，RAG 任务会明确失败并提示配置方式，而不是返回看似成功的空结果。

## 配置参考

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| OFFICE_AGENT_HOST | 127.0.0.1 | API 监听地址 |
| OFFICE_AGENT_PORT | 8765 | API 监听端口 |
| OFFICE_AGENT_DEBUG | false | 调试模式 |
| OFFICE_AGENT_DATA_DIR | 按运行模式决定 | 统一应用数据根目录 |
| OFFICE_AGENT_UPLOAD_DIR | <data-root>/uploads | 上传目录 |
| OFFICE_AGENT_OUTPUT_DIR | <data-root>/outputs | 生成结果目录 |
| OFFICE_AGENT_LOG_DIR | <data-root>/logs | 日志目录 |
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
- API Key 使用 Fernet 加密落盘，旧版密文仅用于兼容读取并在成功读取后迁移；
- 密码哈希使用 PBKDF2-HMAC-SHA256，支持旧版密码在登录时安全升级；
- 请求模型会过滤敏感字段，任务选项拒绝客户端伪造服务端路径、凭据和网络能力；
- 文件上传包含扩展名、大小、路径和生命周期控制，并支持文件隔离、版本和回收站；
- Prompt 安全层会对模型控制符、可疑指令和外部/文件内容做分级处理；
- 认证、RBAC、速率限制、审计日志、错误脱敏和请求大小限制属于 API 安全层；
- 默认不把“本地子进程 + 临时目录 + 关键词过滤”宣称为操作系统级沙箱。生产环境在接入具备网络禁用、只读文件系统、CPU/内存配额和独立身份的外部执行器前，应保持危险执行能力关闭。

安全设计记录见 [office_agent/security/README.md](office_agent/security/README.md) 和 [docs/architecture/accepted_design_decisions.md](docs/architecture/accepted_design_decisions.md)。RAG 运行时边界见 [docs/architecture/rag_embedding_runtime.md](docs/architecture/rag_embedding_runtime.md)。

## 测试与质量检查

后端全量测试：

~~~powershell
python -m pytest tests/ -m "not slow" --tb=short
~~~

常用静态检查：

~~~powershell
mypy
ruff check office_agent/ --select=E,F --ignore=E501
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

## Windows 发布构建

Windows 发布入口是 [desktop/build_windows.bat](desktop/build_windows.bat)：

~~~powershell
python -m pip install -r requirements-production.txt
pnpm --dir desktop-client install --frozen-lockfile
desktop/build_windows.bat
~~~

发布脚本会：

1. 检查 Python、PyInstaller、pnpm 和 Cargo；
2. 要求已跟踪工作区干净，并记录当前 Git HEAD；
3. 用 PyInstaller 构建 dist/OfficeAgent/；
4. 生成并校验包含源码 SHA、文件大小和 SHA-256 的完整 release manifest；
5. 通过 Tauri 构建 Windows 安装包，并在构建前后执行来源守卫。

如果 manifest 缺失、源码 SHA 不一致、文件被篡改、出现未记录文件或构建输入被修改，发布链会以非零状态终止。该流程是 Windows 发布校验链，不等同于代码签名、SmartScreen 或真实 Office GUI 打开/编辑/保存验收。

## 项目结构

~~~text
OfficeAgent/
├── office_agent/                 # Python 后端与领域能力
│   ├── api/                      # FastAPI、路由、中间件、OpenAPI
│   ├── database/                 # SQLite、SQLAlchemy、Alembic、Repository
│   ├── model_gateway/            # 文本模型客户端、路由与故障转移
│   ├── vision_gateway/           # 图片、PDF、PPT 与扫描文档理解
│   ├── services/                 # Word 等通用文档服务
│   ├── ppt_agent/                # PPT 编排、生成与质量检查
│   ├── excel_agent/              # Excel 分析、公式、图表与模板
│   ├── knowledge_base/           # 解析、分块、Embedding、向量检索
│   ├── task_queue/               # 本地 Worker、调度器和任务实现
│   ├── security/                 # 认证、RBAC、Prompt、文件、审计、安全边界
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

## 开发约定

- 任何模型密钥、用户文件、数据库和构建产物都不应提交到 Git；
- 修改 API、任务映射、配置来源或数据模型时，同时补充对应测试；
- 处理外部输入时保持显式失败、错误脱敏和边界校验，不要用“看起来成功”的空结果掩盖失败；
- 发布前使用统一的 desktop/build_windows.bat，不要绕过 SHA manifest 和来源守卫；
- 涉及安全能力时，区分应用层策略和真正的 OS/容器隔离，不夸大防护强度。

## 许可证

本项目使用 MIT License。
