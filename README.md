# Office Agent（本地桌面版）

智能办公自动化助手：Word 排版、PPT 生成、Excel 数据分析，多模型 AI 驱动，本地运行、数据不出本机。

## 架构

- **后端** `office_agent/`：FastAPI，默认 SQLite + 本地线程池任务队列 + `urllib` 零 SDK 模型网关（多提供商 + 故障转移）。
- **桌面壳** `desktop-client/`：Tauri 2 + React 19 + Vite，内嵌后端（`src-tauri` 自动拉起并健康检查后端进程）。
- **本地模式** `office_agent/local/`：本地身份 / 存储 / 配置 / 任务队列 / 运行时 / 更新。
- 详细设计见 [LOCAL_ARCHITECTURE.md](LOCAL_ARCHITECTURE.md)。

## 运行

```bash
# 后端（默认 http://127.0.0.1:8765，文档 /docs）
python -m office_agent.api.main

# 前端（开发）
cd desktop-client
pnpm install
pnpm run dev
```

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

0.49.0
