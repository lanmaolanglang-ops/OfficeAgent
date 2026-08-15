"""
OfficeAgent Desktop Launcher
桌面启动器 - 启动Backend并管理生命周期
"""
import os
import sys
import time
import signal
import threading
from pathlib import Path

# 确保项目路径在sys.path中
if getattr(sys, 'frozen', False):
    # PyInstaller打包后
    APP_DIR = Path(sys.executable).parent
    sys.path.insert(0, str(APP_DIR))
else:
    APP_DIR = Path(__file__).parent.parent
    sys.path.insert(0, str(APP_DIR))

# 设置本地模式环境变量
os.environ["OFFICE_AGENT_LOCAL"] = "1"
os.environ["AUTH_MODE"] = "local"


def main():
    """主入口"""
    print(f"OfficeAgent v0.49.0 - Local Desktop Mode")
    print(f"App directory: {APP_DIR}")
    try:
        from office_agent.local import LocalApplication, AuthMode
        # 初始化本地应用
        app = LocalApplication()
        app.initialize()
        info = app.get_info()
        print(f"User: {info['user']['display_name']}")
        print(f"Data dir: {info['data_dir']}")
        # 启动Backend
        runtime = app.runtime
        config = runtime.config
        print(f"Starting backend at {config.host}:{config.port}...")
        if runtime.start():
            print(f"Backend running at {runtime.state.url}")
            print(f"API docs: {runtime.state.url}/docs")
        else:
            print(f"Failed to start backend: {runtime.state.last_error}")
            # 即使backend启动失败，保持进程运行以便查看错误
        # 等待退出信号
        stop_event = threading.Event()
        def signal_handler(sig, frame):
            print("\nShutting down...")
            stop_event.set()
        signal.signal(signal.SIGINT, signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, signal_handler)
        print("OfficeAgent is running. Press Ctrl+C to stop.")
        try:
            while not stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        # 关闭
        print("Stopping...")
        app.shutdown()
        print("OfficeAgent stopped.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        if getattr(sys, 'frozen', False):
            input("Press Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    main()
