"""
OfficeAgent Desktop Launcher
桌面应用入口点 - 启动Runtime Manager并保持运行
支持PyInstaller打包后直接运行
"""
import os
import sys
import signal
import logging
import argparse
from pathlib import Path

APP_VERSION = "0.51.1"

# ============================================================
# PyInstaller GUI 模式兼容：sys.stdout / sys.stderr 可能为 None
# ============================================================

class _NullStream:
    """空输出流，避免 isatty / write / flush 报错"""
    def write(self, data): pass
    def flush(self): pass
    def isatty(self): return False
    def fileno(self): return -1

if sys.stderr is None:
    sys.stderr = _NullStream()
if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stdin is None:
    sys.stdin = _NullStream()

# ============================================================
# 路径设置
# ============================================================
FROZEN = getattr(sys, 'frozen', False)
if FROZEN:
    APP_DIR = Path(sys.executable).parent
    sys.path.insert(0, str(APP_DIR))
    internal_dir = APP_DIR / "_internal"
    if internal_dir.exists():
        sys.path.insert(0, str(internal_dir))
else:
    APP_DIR = Path(__file__).parent.parent
    sys.path.insert(0, str(APP_DIR))


def setup_logging(log_dir: Path):
    """配置日志 - 使用Python标准logging，不依赖uvicorn的log_config"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "office_agent.log"
    handlers = [logging.FileHandler(str(log_file), encoding="utf-8")]
    if not FROZEN:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )
    # 降低第三方库日志级别
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.INFO)


def run_uvicorn_direct(host: str, port: int, data_dir: Path):
    """在当前进程直接运行uvicorn（frozen模式使用）"""
    logger = logging.getLogger("office_agent.launcher")
    logger.info("Importing uvicorn...")
    import uvicorn

    # 设置环境变量
    os.environ["OFFICE_AGENT_LOCAL"] = "1"
    os.environ["AUTH_MODE"] = "local"
    os.environ["OFFICE_AGENT_DATA_DIR"] = str(data_dir)
    os.environ["OFFICE_AGENT_LOG_DIR"] = str(data_dir / "logs")
    os.environ["OFFICE_AGENT_VERSION"] = APP_VERSION

    # 直接导入app对象（frozen模式下字符串导入不可靠）
    logger.info("Importing office_agent.api.main...")
    from office_agent.api.main import app

    logger.info(f"Starting uvicorn on {host}:{port}...")
    # 关键：log_config=None 避免 uvicorn 重新配置日志导致 sys.stderr 问题
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        log_config=None,
    )


def main():
    parser = argparse.ArgumentParser(description="OfficeAgent Desktop")
    parser.add_argument("--port", type=int, default=8765, help="Backend port")
    parser.add_argument("--host", default="127.0.0.1", help="Backend host")
    parser.add_argument("--data-dir", type=str, default=None, help="Data directory")
    parser.add_argument("--no-autostart", action="store_true", help="Don't auto-start backend")
    parser.add_argument("--background", action="store_true", help="Run in background")
    args = parser.parse_args()

    # 数据目录
    if args.data_dir:
        data_dir = Path(args.data_dir)
    elif sys.platform == "win32":
        data_dir = Path(os.environ.get("APPDATA", Path.home())) / "OfficeAgent"
    else:
        data_dir = Path.home() / ".officeagent"

    setup_logging(data_dir / "logs")
    logger = logging.getLogger("office_agent.launcher")
    logger.info(f"Starting OfficeAgent v{APP_VERSION} (frozen={FROZEN})")
    logger.info(f"Data dir: {data_dir}")
    logger.info(f"App dir: {APP_DIR}")
    logger.info(f"Python: {sys.executable}")

    # 启动Backend
    if args.no_autostart:
        logger.info("Backend autostart disabled, exiting")
        return

    if FROZEN:
        # Frozen模式：直接在当前进程运行uvicorn
        try:
            run_uvicorn_direct(args.host, args.port, data_dir)
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
        except Exception as e:
            logger.error(f"Backend failed: {e}", exc_info=True)
            raise
    else:
        # 开发模式：通过RuntimeManager启动子进程
        from office_agent.runtime_manager import AppConfig, ApplicationRuntimeManager
        config = AppConfig(
            host=args.host,
            port=args.port,
            data_dir=data_dir,
            backend_dir=APP_DIR,
            auto_start_backend=True,
        )
        mgr = ApplicationRuntimeManager(config)

        def signal_handler(sig, frame):
            logger.info("Shutdown signal received")
            mgr.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, signal_handler)

        if not mgr.start():
            logger.error("Failed to start backend")
            sys.exit(1)
        logger.info(f"Backend running at {mgr.state.backend_url}")

        try:
            mgr.wait_for_shutdown()
        except KeyboardInterrupt:
            pass
        finally:
            mgr.stop()
            logger.info("OfficeAgent stopped")


if __name__ == "__main__":
    main()
