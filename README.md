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
# 一次性 Windows 发布构建（后端 + manifest 校验 + Tauri）
python -m pip install -r requirements-production.txt
pnpm --dir desktop-client install --frozen-lockfile
desktop\build_windows.bat
```

发布入口要求已跟踪工作区干净。它从同一个 Git HEAD 构建
dist/OfficeAgent/，生成覆盖整个后端目录的 release-manifest.json
（完整源码 SHA、文件大小和 SHA-256），并在 Tauri 打包前再次阻断式校验。
manifest 缺失、源码 SHA 不一致、文件被篡改或出现未记录文件时均会失败。
pnpm --dir desktop-client run tauri:build 是内部 Tauri 入口，只接受已经由
上述统一入口生成且通过校验的后端产物。

## 版本

0.50.0
