"""
Windows Service Manager - Windows服务管理
支持安装/卸载/启动/停止OfficeAgent后台服务
"""
import os
import sys
import time
import logging
import argparse
import subprocess
from pathlib import Path

logger = logging.getLogger("office_agent.service")

SERVICE_NAME = "OfficeAgent"
SERVICE_DISPLAY_NAME = "OfficeAgent Backend Service"
SERVICE_DESCRIPTION = "OfficeAgent本地AI办公助手后台服务"


def is_admin() -> bool:
    """检查是否管理员权限"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin():
    """以管理员权限重新运行"""
    import ctypes
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )
    sys.exit(0)


def install_service(app_dir: Path, port: int = 8765):
    """安装Windows服务"""
    if not is_admin():
        print("需要管理员权限，正在请求提升...")
        run_as_admin()
        return
    try:
        import win32serviceutil
        import win32service
        import win32event
        import servicemanager
    except ImportError:
        print("安装pywin32: pip install pywin32")
        return False
    # 创建服务脚本
    service_script = app_dir / "service_wrapper.py"
    script_content = f'''
import sys
import os
sys.path.insert(0, r"{app_dir}")
os.chdir(r"{app_dir}")
os.environ["OFFICE_AGENT_LOCAL"] = "1"
os.environ["AUTH_MODE"] = "local"
os.environ["OFFICE_AGENT_DATA_DIR"] = r"{Path(os.environ.get("APPDATA", "")) / "OfficeAgent"}"
import servicemanager
import win32event
import win32service
import win32serviceutil
import threading
import time

class OfficeAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "{SERVICE_NAME}"
    _svc_display_name_ = "{SERVICE_DISPLAY_NAME}"
    _svc_description_ = "{SERVICE_DESCRIPTION}"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self._mgr = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        if self._mgr:
            self._mgr.stop()

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ""))
        from office_agent.runtime_manager import AppConfig, ApplicationRuntimeManager
        config = AppConfig(port={port}, backend_dir=r"{app_dir}")
        self._mgr = ApplicationRuntimeManager(config)
        self._mgr.start()
        while True:
            rc = win32event.WaitForSingleObject(self.hWaitStop, 5000)
            if rc == win32event.WAIT_OBJECT_0:
                break

if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(OfficeAgentService)
'''
    service_script.write_text(script_content, encoding="utf-8")
    # 安装服务
    try:
        result = subprocess.run(
            [sys.executable, str(service_script), "install"],
            capture_output=True, text=True, cwd=str(app_dir),
        )
        if result.returncode == 0:
            # 设置自动启动
            subprocess.run(
                [sys.executable, str(service_script), "--startup", "auto", "update"],
                capture_output=True, text=True,
            )
            print(f"✅ 服务 {SERVICE_NAME} 安装成功")
            return True
        else:
            print(f"❌ 安装失败: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ 安装失败: {e}")
        return False


def uninstall_service(app_dir: Path):
    """卸载Windows服务"""
    if not is_admin():
        print("需要管理员权限")
        run_as_admin()
        return
    service_script = app_dir / "service_wrapper.py"
    if service_script.exists():
        try:
            subprocess.run(
                [sys.executable, str(service_script), "stop"],
                capture_output=True, text=True, timeout=10,
            )
            result = subprocess.run(
                [sys.executable, str(service_script), "remove"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(f"✅ 服务 {SERVICE_NAME} 卸载成功")
                return True
        except Exception as e:
            print(f"卸载错误: {e}")
    # 尝试sc命令
    try:
        subprocess.run(["sc", "stop", SERVICE_NAME], capture_output=True, timeout=10)
        subprocess.run(["sc", "delete", SERVICE_NAME], capture_output=True, timeout=10)
        print(f"✅ 服务 {SERVICE_NAME} 已删除")
        return True
    except Exception as e:
        print(f"❌ 删除失败: {e}")
        return False


def start_service():
    """启动服务"""
    try:
        result = subprocess.run(["sc", "start", SERVICE_NAME], capture_output=True, text=True, timeout=30)
        if "RUNNING" in result.stdout or result.returncode == 0:
            print(f"✅ 服务 {SERVICE_NAME} 已启动")
            return True
        print(f"启动结果: {result.stdout}")
        return False
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        return False


def stop_service():
    """停止服务"""
    try:
        result = subprocess.run(["sc", "stop", SERVICE_NAME], capture_output=True, text=True, timeout=30)
        print(f"✅ 服务 {SERVICE_NAME} 已停止")
        return True
    except Exception as e:
        print(f"❌ 停止失败: {e}")
        return False


def service_status():
    """查看服务状态"""
    try:
        result = subprocess.run(
            ["sc", "query", SERVICE_NAME], capture_output=True, text=True, timeout=10
        )
        print(result.stdout)
        return "RUNNING" in result.stdout
    except Exception as e:
        print(f"查询失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="OfficeAgent Service Manager")
    parser.add_argument("action", choices=["install", "uninstall", "start", "stop", "status", "restart"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--app-dir", type=str, default=None)
    args = parser.parse_args()
    app_dir = Path(args.app_dir) if args.app_dir else Path(__file__).parent.parent
    if args.action == "install":
        install_service(app_dir, args.port)
    elif args.action == "uninstall":
        uninstall_service(app_dir)
    elif args.action == "start":
        start_service()
    elif args.action == "stop":
        stop_service()
    elif args.action == "status":
        service_status()
    elif args.action == "restart":
        stop_service()
        time.sleep(2)
        start_service()


if __name__ == "__main__":
    main()
