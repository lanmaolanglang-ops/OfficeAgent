# Office Agent（本地桌面版）

智能办公自动化助手：Word 排版、PPT 生成、Excel 数据分析，多模型 AI 驱动，本地运行、数据不出本机。

## 架构

- **后端** `office_agent/`：FastAPI，默认 SQLite + 本地线程池任务队列 + `urllib` 零 SDK 模型网关（多提供商 + 故障转移）。
- **桌面壳** `desktop-client/`：Tauri 2 + React 19 + Vite，内嵌后端（`src-tauri` 自动拉起并健康检查后端进程）。
- **桌面启动器** `desktop/`：PyInstaller 打包入口（`app_launcher.py`），拉起 FastAPI 后端并管理进程生命周期。

## 运行

```bash
# 后端（默认 http://127.0.0.1:8765，文档 /docs）
python -m office_agent.api.main

# 前端（开发）
cd desktop-client
pnpm install
pnpm run dev
```

## 配置与安全

- **API Key**：保存在本机 `~/.office_agent/models.json`（Fernet 加密），**不会进入 Git 仓库**。请勿在代码或提交中写入任何真实密钥。
- **认证**：默认关闭（本地模式）。如需开启，请配置 `auth_enabled` 与 `api_keys`，并用环境变量 `OFFICE_AGENT_JWT_SECRET` 覆盖默认占位密钥。
- **默认 JWT 密钥**：`office_agent/security/config.py` 中是开发占位符，生产环境务必用环境变量覆盖。

## 测试

```bash
python -m pytest tests/
```

## 打包

```bash
# 1) 后端 → dist/OfficeAgent/（PyInstaller）
python -m pip install -r requirements-production.txt pyinstaller
pyinstaller office_agent.spec --clean --noconfirm

# 2) 桌面安装包（Tauri NSIS）
cd desktop-client
pnpm install
npm run tauri:build
```

## 版本

0.50.0
