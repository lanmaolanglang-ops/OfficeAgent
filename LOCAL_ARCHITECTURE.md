# OfficeAgent 本地桌面架构文档 v0.49.0

## 概述

OfficeAgent 从云 SaaS 架构迁移至本地桌面架构。用户安装 `OfficeAgent.exe` 即可运行完整系统，无需注册登录，所有数据保存在本地。

> ⚠️ **现状说明（v0.49.0）**：本文档主要描述 `office_agent/local/` 子系统。该子系统在 v0.47.5 迁移时搭建，但**尚未接入 FastAPI HTTP 层**——产品实际运行的 API 走的是另一套并行实现：
> - 数据层：`office_agent/database/`（SQLAlchemy，`~/.office_agent/db/office_agent.db`）
> - 文件存储：`office_agent/storage/`（StorageService）
> - 模型网关：`office_agent/models/` + `office_agent/model_gateway/`（provider 枚举为 openai/claude/gemini/deepseek/doubao/qwen/agnes/custom）
> - 任务队列：`office_agent/task_queue/`（线程池 LocalWorker）
>
> `office_agent/local/` 目前只有 `desktop/` 启动器（`app_launcher.py`）引用了其中的运行时管理。阅读本文档的「API 变化」与「模块详解」时请以代码为准。

## 目标架构

```
                OfficeAgent.exe
                       |
                  Desktop Runtime
                       |
        --------------------------------
        Local Frontend (未来)
        Local Backend (FastAPI on 127.0.0.1:8765)
        Agent Runtime
        File Manager (LocalFileStorage)
        Model Manager
        Config Manager
        --------------------------------
                       |
                  AI Provider API
            GPT / Claude / DeepSeek / 豆包 / 千问
```

## 核心设计原则

1. **打开即用** - 无需注册登录，默认本地用户
2. **数据本地** - 所有文件、配置、数据库保存在用户机器
3. **隐私安全** - API Key 加密存储，不上传任何数据
4. **保留能力** - 不修改 Word/PPT/Excel Agent 和 Workflow Engine
5. **云扩展** - Auth Adapter 设计支持未来切换云端模式
6. **朋友共享** - 支持多用户配置（同一机器不同用户目录）

## 模块详解

### 1. Local Identity System（本地身份系统）

**位置**: `office_agent/local/auth/identity.py`

- **LocalUserProfile**: 本地用户配置
  - `user_id`: 格式 `local_{uuid.hex[:12]}`
  - `username`: 默认 `local_user`
  - `settings`: 用户设置（主题、语言等）
  - `created_at` / `last_active_at`
- **LocalIdentityManager**: 管理用户配置的加载/创建/保存
- 数据存储: `%APPDATA%/OfficeAgent/user_profile.json`
- 首次启动自动创建用户，无需注册

### 2. Auth Adapter（认证适配器）

**位置**: `office_agent/local/auth/adapter.py`

```
AuthAdapter
├── LocalAuthProvider (默认)
│   └── 无需登录，总是认证成功，权限 ["*"]
└── CloudAuthProvider (保留)
    └── JWT认证，需要用户名密码
```

- 通过 `AUTH_MODE` 环境变量切换（`local`/`cloud`）
- 本地模式下所有 API 不需要认证头
- 保留完整 RBAC 接口，云端模式直接复用
- `has_permission()` 本地模式默认返回 True

### 3. Local File Storage（本地文件存储）

**位置**: `office_agent/local/storage/local_storage.py`

目录结构:
```
%APPDATA%/OfficeAgent/
├── data/
│   ├── documents/    # 用户上传文档
│   ├── outputs/      # 处理结果
│   ├── templates/    # 模板文件
│   ├── cache/        # 缓存
│   ├── temp/         # 临时文件（自动清理）
│   └── exports/      # 导出文件
├── database/
│   └── officeagent.db
├── logs/
│   ├── backend.log
│   └── audit.log
├── config/
│   ├── config.json
│   ├── credentials.enc
│   └── salt.bin
└── user_profile.json
```

功能:
- 文件索引（file_index.json）
- SHA256 校验和
- MIME 类型检测
- 重名自动处理
- 存储统计（含磁盘空间）
- 临时文件自动清理
- 支持自定义存储路径（迁移）

### 4. Local SQLite Database（本地数据库）

> ⚠️ **现状说明**：本小节描述 local 子系统自带的 sqlite3 库（`%APPDATA%/OfficeAgent/database/officeagent.db`），**未被 FastAPI 后端使用**。后端实际读写 SQLAlchemy 库 `~/.office_agent/db/office_agent.db`（`office_agent/database/`），两套库并行、互不相连。

**位置**: `office_agent/local/database/local_db.py`

- 默认 SQLite，WAL 模式，线程安全
- 保留数据库抽象层，未来可切换 PostgreSQL
- 表结构:

| 表名 | 用途 |
|------|------|
| `schema_version` | 数据库版本 |
| `user_profile` | 用户配置 |
| `app_settings` | 应用设置 |
| `model_configs` | 模型配置 |
| `tasks` | 任务记录 |
| `audit_logs` | 审计日志 |

- 自动创建索引
- 支持 JSON 字段序列化

### 5. Local Credential Manager（凭据管理器）

**位置**: `office_agent/local/credential/credential_manager.py`

- **禁止明文存储** API Key
- 加密方案:
  - 优先使用 `cryptography.Fernet`（AES-128-CBC + HMAC）
  - 降级方案: XOR + Base64（基础保护）
- 密钥派生: PBKDF2-HMAC-SHA256, 100,000 iterations
- 密钥材料: 机器信息（计算机名+用户名+CPU+路径）
- 支持的供应商: OpenAI, Anthropic, DeepSeek, 豆包, 千问, 智谱, Moonshot, Ollama
- 功能: 增删改查、掩码显示、验证、全部清除
- 文件权限: 0600（仅所有者可读写）

### 6. Model Manager（模型管理中心）

**位置**: `office_agent/local/models/model_manager.py`

- 预设 8 个供应商配置
- 每个供应商预设可用模型列表
- 功能:
  - 设置/获取 API Key（通过 Credential Manager）
  - 启用/禁用模型
  - 设置默认模型
  - Fallback 链配置
  - 模型参数（max_tokens, temperature, top_p, timeout）
  - 成本统计
  - 连接测试
- 预设供应商:

| 供应商 | 默认模型 | 视觉支持 |
|--------|----------|----------|
| OpenAI | gpt-4o | ✅ |
| Anthropic | claude-3-5-sonnet | ✅ |
| DeepSeek | deepseek-chat | ❌ |
| 豆包 | doubao-pro-32k | ✅ |
| 千问 | qwen-turbo | ✅ |
| 智谱 | glm-4 | ✅ |
| Moonshot | moonshot-v1-8k | ❌ |
| Ollama | llama3.1 | ❌ |

> ⚠️ **现状说明**：上表是 local 子系统 `model_manager.py` 的预设，**与 API 实际模型网关不一致**。API 网关（`office_agent/models/model_schemas.py`）的 provider 枚举为 `openai / claude / gemini / deepseek / doubao / qwen / agnes / custom`，且 `/api/settings/model` 的白名单仅接受 `openai / deepseek / doubao / qwen / claude / gemini / agnes`——上表中的 `anthropic`（API 用 `claude`）、`zhipu`、`moonshot`、`ollama` 均会被 API 拒绝。API 侧默认模型为 `gpt-4o`、`claude-3-5-sonnet-20241022`、`deepseek-chat`、`doubao-pro-32k`、`qwen-max`、`gemini-1.5-pro`、`agnes-2.5-flash`。

### 7. Local Task Queue（本地任务队列）

**位置**: `office_agent/local/tasks/local_queue.py`

- 基于 `ThreadPoolExecutor`，不依赖 Celery/Redis
- 功能:
  - 任务优先级（LOW/NORMAL/HIGH/CRITICAL）
  - 任务取消
  - 超时控制
  - 自动重试
  - 进度回调
  - 任务状态持久化到 SQLite
- 默认 worker 数: min(CPU*2, 8)
- 任务类型: Word排版、PPT生成、Excel分析等
- 自动清理完成的旧任务

### 8. Local Runtime Manager（运行时管理器）

**位置**: `office_agent/local/runtime/runtime_manager.py`

- 管理 uvicorn 子进程生命周期
- 功能:
  - 启动 Backend（127.0.0.1:8765）
  - 停止 Backend
  - 健康检查（/health）
  - 端口占用检测
  - 异常自动重启（最多5次）
  - 日志管理（logs/backend.log）
  - 状态监控线程
- 启动流程:
  1. 检查端口
  2. 启动 uvicorn 子进程
  3. 等待健康检查通过（最多30秒）
  4. 启动监控线程
  5. 定期健康检查，失败则重启

### 9. Local Config Manager（配置管理器）

**位置**: `office_agent/local/config/local_config.py`

- 配置文件: `config/config.json`
- 支持点分路径访问: `config.get("models.default_model")`
- 配置分类:
  - `app`: 应用设置（语言、主题、自启）
  - `paths`: 文件路径（支持自定义）
  - `models`: 模型配置
  - `agents`: Word/PPT/Excel Agent 设置
  - `runtime`: 运行时设置（端口、worker数）
  - `security`: 安全设置（沙箱、审计）
  - `storage`: 存储设置（缓存大小、清理）
  - `ui`: 界面设置
- 支持导入/导出配置
- 默认值自动合并

### 10. Environment Checker（环境检测器）

**位置**: `office_agent/local/env/env_checker.py`

检查项:
- Python 版本（>=3.10）
- 操作系统（Windows/macOS/Linux）
- 必需依赖（python-docx, python-pptx, openpyxl, pandas, fastapi, uvicorn 等）
- 可选依赖（cryptography, psutil 等）
- 磁盘空间（>=5GB）
- 目录读写权限
- Microsoft Office 组件（Windows）
- 网络连接
- 数据目录完整性

### 11. Update Manager（升级管理器）

**位置**: `office_agent/local/update/update_manager.py`

- 检查更新（HTTP API）
- 版本比较（语义化版本）
- 下载更新包（进度回调）
- 安装更新框架
- 更新历史记录
- 强制更新支持
- 更新服务器 URL 可配置

### 12. Security（安全模块调整）

保留的安全功能:
- ✅ Prompt Injection 防护
- ✅ Sandbox 沙箱执行
- ✅ Tool Permission 工具权限
- ✅ File Security 文件安全扫描
- ✅ Audit Log 审计日志（本地 logs/audit.log + SQLite）

调整:
- ❌ 取消多租户隔离
- ❌ 取消 JWT 强制认证
- ✅ 本地权限控制（文件/Agent工具/API）

## 数据流

### 启动流程

```
1. OfficeAgent.exe 启动
   ↓
2. LocalApplication.initialize()
   ├── LocalConfigManager 加载 config.json
   ├── LocalIdentityManager 创建/加载本地用户
   ├── AuthAdapter 初始化为 Local 模式
   ├── LocalFileStorage 初始化目录结构
   ├── LocalDatabase 连接 SQLite，创建表
   ├── LocalCredentialManager 加载加密凭据
   ├── ModelManager 加载模型配置
   ├── LocalTaskQueue 初始化线程池
   └── RuntimeManager 启动 uvicorn Backend
   ↓
3. Backend 监听 127.0.0.1:8765
   ↓
4. Frontend（未来）连接 API
```

### 任务执行流程

```
1. 用户提交任务（Word排版/PPT生成/Excel分析）
   ↓
2. API 接收请求 → LocalAuthProvider 认证（总是通过）
   ↓
3. LocalTaskQueue.submit() 提交到线程池
   ↓
4. Agent 执行任务
   ├── 从 ModelManager 获取模型配置和 API Key
   ├── 从 LocalFileStorage 读取输入文件
   ├── 调用 AI Provider API
   └── 结果保存到 LocalFileStorage
   ↓
5. 任务状态更新到 SQLite
   ↓
6. 返回结果给用户
```

## 目录结构

```
office_agent/local/
├── __init__.py              # 模块入口，LocalApplication 统一初始化
├── auth/
│   ├── __init__.py
│   ├── identity.py          # LocalUserProfile, LocalIdentityManager
│   └── adapter.py           # AuthAdapter, LocalAuthProvider, CloudAuthProvider
├── storage/
│   ├── __init__.py
│   └── local_storage.py     # LocalFileStorage, FileInfo
├── database/
│   ├── __init__.py
│   └── local_db.py          # LocalDatabase (SQLite)
├── credential/
│   ├── __init__.py
│   └── credential_manager.py # API Key 加密存储
├── models/
│   ├── __init__.py
│   └── model_manager.py     # 模型供应商管理
├── tasks/
│   ├── __init__.py
│   └── local_queue.py       # 线程池任务队列
├── runtime/
│   ├── __init__.py
│   └── runtime_manager.py   # uvicorn 进程管理
├── config/
│   ├── __init__.py
│   └── local_config.py      # config.json 管理
├── env/
│   ├── __init__.py
│   └── env_checker.py       # 环境检测
└── update/
    ├── __init__.py
    └── update_manager.py    # 版本升级
```

## 部署方案

### Windows 打包

1. **PyInstaller 打包**: `pyinstaller office_agent.spec`
   - 单目录模式（COLLECT）
   - 包含所有依赖
   - 无控制台窗口（console=False）
   - 排除不必要的包（celery, redis, psycopg2 等）

2. **Tauri 桌面安装包（NSIS）**: `cd desktop-client && npm run tauri:build`
   - 生成 `desktop-client/src-tauri/target/release/bundle/nsis/*-setup.exe`
   - 内嵌 PyInstaller 后端（`dist/OfficeAgent/` 作为资源打包进 `backend/`）

3. **构建脚本**: `desktop/build_windows.bat`（后端 PyInstaller 打包）

### 数据目录

- Windows: `%APPDATA%\OfficeAgent\`
- macOS: `~/Library/Application Support/OfficeAgent/`
- Linux: `~/.local/share/OfficeAgent/`

## API 变化

> ⚠️ 本节如实列出 v0.49.0 实际暴露的端点（来自 `office_agent/api/router/`）。此前文档曾声称存在 `/api/local/*`、`/api/runtime/*` 等 12 个端点，实际**从未实现**——`office_agent/local/` 子系统无 HTTP 暴露层。

### 实际端点

- 服务状态（`router/health.py`，无前缀）：`GET /`、`GET /health`、`GET /api/health`、`GET /api/version`、`GET /ready`、`GET /live`；另有 `GET /metrics` 与 `/api/logs/*`、`/api/trace/{trace_id}`（在 `api/main.py`）
- 对话（`router/chat.py`）：`POST /api/chat`
- Agent（`router/agent.py`）：`GET /api/agents`、`GET /api/agents/{agent_id}`、`GET /api/agents/{agent_id}/versions`
- 任务（`router/task.py`）：`POST /api/task/create`、`GET /api/task/{task_id}`、`GET /api/task/`、`POST /api/task/{task_id}/cancel`、`POST /api/task/{task_id}/feedback`
- 文件（`router/file.py`）：`POST /api/file/upload`、`GET /api/file/download/{file_id}`、`GET /api/file/{file_id}`、`GET /api/file/{file_id}/versions`、`POST /api/file/{file_id}/versions/{v}/restore`、`DELETE /api/file/{file_id}`、`GET /api/file/`、`GET /api/file/stats/overview`、`POST /api/file/cleanup` 及分片上传端点
- 设置（`router/settings.py`）：`GET|POST /api/settings/model`、`POST /api/settings/model/default`、`GET|POST /api/settings/image-model`
- 配置（`router/config.py`）：`GET /api/config/`、`/export`、`/reload`、`/models`、`/agents`、`/prompts`、`/skills`、`/workflows` 等

### 认证

- 本地模式：所有请求不需要 Authorization 头

## 版本历史

| 版本 | 内容 |
|------|------|
| v0.47.5 | 本地桌面架构迁移：Auth/Storage/Database/Credential/Models/Tasks/Runtime/Config/Env/Update |
| v0.49.0 | 收敛为纯本地桌面：删除云部署栈与僵尸模块，统一端口/版本，修复运行时硬伤，补齐打包与 CI |
