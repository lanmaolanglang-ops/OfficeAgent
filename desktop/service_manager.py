"""
Windows Service Manager - Windows服务管理
支持安装/卸载/启动/停止OfficeAgent后台服务
"""
import sys
import time
import logging
import argparse
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from office_agent.runtime_config import get_desktop_data_root  # noqa: E402

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


def elevated_command_line(argv: list[str]) -> str:
    """按 Windows 命令行引用规则渲染"脚本 + 参数"命令行。

    旧实现 ``" ".join(sys.argv)`` 对含空格/引号/末尾反斜杠的路径
    （例如 ``--app-dir "C:\\Program Files\\App\\"``）会被提权后的解释器
    重新切分成错误参数。统一走 stdlib ``subprocess.list2cmdline``
    （微软官方规则：空格包裹、内嵌引号加倍、末尾反斜杠转义）。
    ``argv[0]`` 是脚本路径，必须保留在参数里——可执行文件是
    ``sys.executable`` 本身，二者不重复。``&`` 等字符仅对 cmd.exe 有
    特殊含义，ShellExecuteW 不经过 shell，无需转义。
    """
    return subprocess.list2cmdline(list(argv))


def run_as_admin():
    """以管理员权限重新运行"""
    import ctypes
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, elevated_command_line(sys.argv), None, 1
    )
    sys.exit(0)


def build_service_wrapper_script(app_dir: Path, port: int,
                                 service_data_dir) -> str:
    """渲染 service_wrapper.py 的完整内容（纯函数，便于对生成代码直接测试）。

    生命周期契约（SvcDoRun / SvcStop）：
    - SvcStop 先于 SvcDoRun 就绪的竞态：SvcStop 时 ``_mgr`` 尚为 None
      无法清理，SvcDoRun 的 finally 必须兜底停止后端；
    - 后端清理恰好一次（取走引用的幂等语义），清理失败只记录事件日志，
      绝不阻塞服务退出；
    - 启动失败收口：记录事件日志、清理半启动状态、明确上报
      SERVICE_STOPPED，而不是以未处理异常结束；
    - 等待停止信号用阻塞的 WaitForSingleObject（5s 超时重查），不是
      busy spin。
    """
    desktop_dir = Path(__file__).resolve().parent
    # 用 repr 嵌入路径/服务名：路径中的引号、反斜杠、换行不得被解释为 Python 语法（P2-54）
    app_dir_lit = repr(str(app_dir))
    desktop_dir_lit = repr(str(desktop_dir))
    service_data_lit = repr(str(service_data_dir))
    service_name_lit = repr(SERVICE_NAME)
    service_display_lit = repr(SERVICE_DISPLAY_NAME)
    service_desc_lit = repr(SERVICE_DESCRIPTION)
    port_lit = repr(int(port))
    return f'''
import sys
import os
sys.path.insert(0, {app_dir_lit})
sys.path.insert(0, {desktop_dir_lit})
os.chdir({app_dir_lit})
from office_agent.runtime_config import apply_desktop_runtime_env
apply_desktop_runtime_env({service_data_lit})
import servicemanager
import win32event
import win32service
import win32serviceutil
import threading
import time

class OfficeAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = {service_name_lit}
    _svc_display_name_ = {service_display_lit}
    _svc_description_ = {service_desc_lit}

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self._mgr = None

    def _stop_backend_once(self):
        # 幂等清理：取走引用，保证每个后端实例只被 stop 一次。
        # SvcStop（另一线程）与 SvcDoRun 退出路径都可能调用。
        mgr = self._mgr
        if mgr is None:
            return
        self._mgr = None
        try:
            mgr.stop()
        except Exception as exc:
            servicemanager.LogErrorMsg(f"OfficeAgent 后端停止失败: {{exc}}")

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        self._stop_backend_once()

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ""))
        try:
            from office_agent.runtime_manager import AppConfig, ApplicationRuntimeManager
            config = AppConfig(port={port_lit}, backend_dir={app_dir_lit})
            self._mgr = ApplicationRuntimeManager(config)
            self._mgr.start()
        except Exception as exc:
            # 启动失败必须收口：记录事件日志、清理半启动状态并明确上报
            # SERVICE_STOPPED，而不是以未处理异常结束。
            servicemanager.LogErrorMsg(f"OfficeAgent 后端启动失败: {{exc}}")
            self._stop_backend_once()
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)
            return
        try:
            while True:
                rc = win32event.WaitForSingleObject(self.hWaitStop, 5000)
                if rc == win32event.WAIT_OBJECT_0:
                    break
        finally:
            # SvcStop 早于后端就绪的竞态（当时 _mgr 为 None、SvcStop 无法
            # 清理）在这里兜底，避免孤儿后端进程。
            self._stop_backend_once()
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)

if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(OfficeAgentService)
'''


def install_service(app_dir: Path, port: int = 8765):
    """安装Windows服务"""
    if not is_admin():
        print("需要管理员权限，正在请求提升...")
        run_as_admin()
        return
    try:
        import win32serviceutil  # type: ignore[import-untyped]  # noqa: F401
        import win32service  # type: ignore[import-untyped]  # noqa: F401
        import win32event  # type: ignore[import-untyped]  # noqa: F401
        import servicemanager  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        print("安装pywin32: pip install pywin32")
        return False
    # 创建服务脚本
    service_script = app_dir / "service_wrapper.py"
    service_data_dir = get_desktop_data_root()
    script_content = build_service_wrapper_script(
        app_dir, port, service_data_dir)
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
        result = subprocess.run(
            ["sc", "stop", SERVICE_NAME], capture_output=True, text=True, timeout=30
        )
        # P3-121: 不能无视 sc 的返回码假装停止成功
        if result.returncode == 0:
            print(f"✅ 服务 {SERVICE_NAME} 已停止")
            return True
        # 1062 = 服务本就未启动，幂等视为已停止
        if result.returncode == 1062 or "has not been started" in (result.stdout or ""):
            print(f"ℹ️ 服务 {SERVICE_NAME} 本就未运行")
            return True
        print(f"停止结果: {result.stdout} {result.stderr}".strip())
        return False
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
