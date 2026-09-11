"""
Application Runtime Manager - 应用运行管理器
桌面应用启动时的核心管理器，负责Backend生命周期
"""
import os
import sys
import time
import json
import signal
import logging
import threading
import subprocess
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Callable, cast
import urllib.request
import urllib.error

from ._version import __version__
from .runtime_config import desktop_runtime_env, get_desktop_data_root

logger = logging.getLogger("office_agent.runtime_manager")


class AppStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"
    RESTARTING = "restarting"
    UPDATING = "updating"


@dataclass
class AppConfig:
    """应用配置"""
    app_name: str = "OfficeAgent"
    app_version: str = __version__
    host: str = "127.0.0.1"
    port: int = 8765
    backend_module: str = "office_agent.api.main:app"
    auto_start_backend: bool = True
    auto_restart: bool = True
    max_restarts: int = 5
    restart_delay: float = 2.0
    restart_reset_after: float = 300.0
    health_check_interval: float = 5.0
    startup_timeout: float = 30.0
    shutdown_timeout: float = 10.0
    data_dir: Optional[Path] = None
    log_dir: Optional[Path] = None
    python_executable: Optional[str] = None
    backend_dir: Optional[Path] = None
    run_in_background: bool = False

    def __post_init__(self):
        if self.data_dir is None:
            self.data_dir = get_desktop_data_root()
        if self.log_dir is None:
            self.log_dir = self.data_dir / "logs"
        if self.backend_dir is None:
            self.backend_dir = Path(__file__).parent.parent
        if self.python_executable is None:
            self.python_executable = sys.executable


@dataclass
class AppState:
    """应用状态"""
    status: AppStatus = AppStatus.STOPPED
    pid: Optional[int] = None
    start_time: float = 0
    uptime_seconds: float = 0
    restart_count: int = 0
    last_error: str = ""
    last_health_check: float = 0
    health_ok: bool = False
    backend_url: str = ""
    version: str = ""


class ApplicationRuntimeManager:
    """
    应用运行管理器
    桌面应用入口，管理Backend进程的完整生命周期
    """

    def __init__(self, config: AppConfig | None = None):
        self.config = config or AppConfig()
        self.state = AppState(
            backend_url=f"http://{self.config.host}:{self.config.port}",
            version=self.config.app_version,
        )
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._status_callbacks: list[Callable] = []
        self._health_failures = 0
        self._max_health_failures = 3
        self._ensure_dirs()

    def _ensure_dirs(self):
        """确保必要目录存在"""
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        (self.config.data_dir / "config").mkdir(exist_ok=True)
        (self.config.data_dir / "data").mkdir(exist_ok=True)
        (self.config.data_dir / "cache").mkdir(exist_ok=True)

    def on_status_change(self, callback: Callable):
        """注册状态变更回调"""
        self._status_callbacks.append(callback)

    def _set_status(self, status: AppStatus, error: str = ""):
        old = self.state.status
        self.state.status = status
        if error:
            self.state.last_error = error
        if status == AppStatus.RUNNING:
            self.state.start_time = time.time()
            self.state.health_ok = True
        elif status == AppStatus.STOPPED:
            self.state.uptime_seconds = time.time() - self.state.start_time if self.state.start_time else 0
            self.state.pid = None
        for cb in self._status_callbacks:
            try:
                cb(status, self.state)
            except Exception:
                # 回调（桌面 GUI 等）边界无法穷举异常类型，保持 broad catch，
                # 但失败必须留痕而非静默。
                logger.warning("状态回调执行失败", exc_info=True)
        logger.info(f"Status: {old.value} -> {status.value}" + (f" ({error})" if error else ""))

    def is_port_in_use(self) -> bool:
        """检查端口是否被占用"""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((self.config.host, self.config.port))
                return False
            except OSError:
                return True

    def check_health(self) -> bool:
        """检查Backend健康状态"""
        url = f"{self.state.backend_url}/health"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    # /health 返回 "healthy"/"degraded"；兼容历史 "ok"
                    self.state.health_ok = data.get("status") in ("healthy", "degraded", "ok")
                    self.state.last_health_check = time.time()
                    self._health_failures = 0
                    return self.state.health_ok
        except Exception:
            pass
        self.state.health_ok = False
        self.state.last_health_check = time.time()
        return False

    def _build_command(self) -> list[str]:
        """构建启动命令"""
        python = self.config.python_executable or sys.executable
        cmd = [
            python,
            "-m", "uvicorn",
            self.config.backend_module,
            "--host", self.config.host,
            "--port", str(self.config.port),
            "--log-level", "info",
        ]
        return cmd

    def _get_env(self) -> dict:
        """获取子进程环境变量"""
        env = os.environ.copy()
        env.update(desktop_runtime_env(
            self.config.data_dir,
            log_dir=self.config.log_dir,
            app_version=self.config.app_version,
        ))
        # Python路径
        backend_dir = str(self.config.backend_dir)
        if backend_dir not in env.get("PYTHONPATH", ""):
            env["PYTHONPATH"] = backend_dir + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _startup_wait_tick(self) -> bool:
        """等待一个启动轮询周期（0.5s）。

        用 ``_stop_event.wait`` 而不是 ``time.sleep``：stop() 可以在等待途中
        立即唤醒本线程，启动等待随即放弃，避免停止后仍无意义地睡满一拍
        （P1-3）。返回 True 表示等待被停止请求中断。
        """
        return self._stop_event.wait(0.5)

    def start(self) -> bool:
        """启动Backend"""
        if (self.state.status == AppStatus.RUNNING
                and self._process is not None
                and self._process.poll() is None):
            # 仅在"确实持有存活子进程"时才视为已启动；状态是 RUNNING 但进程
            # 已死/缺失时必须继续走启动流程，否则会空转返回 True（P1-3）。
            logger.info("Already running")
            return True
        if self.is_port_in_use():
            # 端口被占用，可能已经在运行
            if self.check_health():
                self._set_status(AppStatus.RUNNING)
                return True
            logger.warning(f"Port {self.config.port} in use but health check failed")
            # 端口被占且占用者不健康：不能保持 RUNNING 假象，必须落到 ERROR
            self._set_status(
                AppStatus.ERROR,
                f"Port {self.config.port} is occupied by an unhealthy process",
            )
            return False
        self._set_status(AppStatus.STARTING)
        log_handle = None
        try:
            cmd = self._build_command()
            env = self._get_env()
            log_file = cast(Path, self.config.log_dir) / "backend.log"
            log_handle = open(log_file, "a", encoding="utf-8")
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self.config.backend_dir),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            self.state.pid = self._process.pid
            logger.info(f"Backend started, PID={self._process.pid}")
            # 等待启动
            start_deadline = time.time() + self.config.startup_timeout
            while time.time() < start_deadline:
                if self._stop_event.is_set():
                    # 启动等待途中收到停止请求：放弃启动，不留孤儿进程
                    logger.info("Startup aborted by stop request")
                    self._kill_process()
                    self._process = None
                    self._set_status(AppStatus.STOPPED)
                    return False
                if self._process.poll() is not None:
                    # 进程已退出
                    self._set_status(AppStatus.ERROR, f"Backend exited with code {self._process.returncode}")
                    return False
                if self.check_health():
                    self._set_status(AppStatus.RUNNING)
                    # 启动监控线程
                    self._stop_event.clear()
                    self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
                    self._monitor_thread.start()
                    return True
                if self._startup_wait_tick():
                    logger.info("Startup aborted by stop request")
                    self._kill_process()
                    self._process = None
                    self._set_status(AppStatus.STOPPED)
                    return False
            self._set_status(AppStatus.ERROR, "Startup timeout")
            self._kill_process()
            return False
        except Exception as e:
            self._set_status(AppStatus.ERROR, str(e))
            return False
        finally:
            # 子进程已继承写句柄，父进程侧的句柄用完即关，避免每次 start 泄漏一个
            if log_handle is not None:
                try:
                    log_handle.close()
                except Exception:
                    pass

    def stop(self, timeout: float | None = None) -> bool:
        """停止Backend"""
        timeout = timeout or self.config.shutdown_timeout
        self._stop_event.set()
        if self._process is None:
            self._set_status(AppStatus.STOPPED)
            return True
        self._set_status(AppStatus.STOPPED)
        try:
            if sys.platform == "win32":
                self._process.terminate()
            else:
                self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("Graceful shutdown timeout, killing process")
                self._kill_process()
        except Exception as e:
            logger.error(f"Error stopping: {e}")
            self._kill_process()
        self._process = None
        return True

    def restart(self) -> bool:
        """重启Backend"""
        self._set_status(AppStatus.RESTARTING)
        self.stop()
        return self._do_restart()

    def _kill_process(self):
        """强制杀死进程"""
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=3)
            except Exception:
                pass

    def _monitor_loop(self):
        """监控线程：检测进程状态和健康"""
        while not self._stop_event.is_set():
            time.sleep(self.config.health_check_interval)
            if self._stop_event.is_set():
                break
            # 检查进程是否存活
            if self._process and self._process.poll() is not None:
                exit_code = self._process.returncode
                logger.warning(f"Backend process exited with code {exit_code}")
                if self.config.auto_restart and self.state.restart_count < self.config.max_restarts:
                    self.state.restart_count += 1
                    self._set_status(AppStatus.RESTARTING, f"Process exited, restarting ({self.state.restart_count}/{self.config.max_restarts})")
                    time.sleep(self.config.restart_delay)
                    self._do_restart()
                else:
                    self._set_status(AppStatus.ERROR, f"Process exited with code {exit_code}")
                break
            # 健康检查
            if not self.check_health():
                self._health_failures += 1
                if self._health_failures >= self._max_health_failures:
                    logger.warning(f"Health check failed {self._health_failures} times, restarting")
                    if self.config.auto_restart and self.state.restart_count < self.config.max_restarts:
                        self.state.restart_count += 1
                        self._do_restart()
                        break
            else:
                self._health_failures = 0
                self.state.uptime_seconds = time.time() - self.state.start_time
                if (self.state.restart_count
                        and self.state.uptime_seconds >= self.config.restart_reset_after):
                    logger.info("Backend stable for %.0fs; resetting restart budget",
                                self.state.uptime_seconds)
                    self.state.restart_count = 0

    def _do_restart(self) -> bool:
        """执行重启，返回新进程是否真正起来了。

        关键：必须先把状态切到 RESTARTING 再 kill。否则状态仍是 RUNNING，
        ``start()`` 的"已运行"短路分支会直接 ``return True``——旧进程已被
        kill、新进程从未创建，监控线程也随之退出，外部却看到 restart 成功
        （P1-3）。
        """
        if self.state.status != AppStatus.RESTARTING:
            self._set_status(AppStatus.RESTARTING)
        self._kill_process()
        self._process = None
        # stop()/上一次停止请求会置 _stop_event；重启是"要拉起来"的意图，
        # 不清掉会让 start() 的启动等待立刻放弃。清掉后仍用可中断等待，
        # 期间若再次收到 stop() 则放弃重启。
        self._stop_event.clear()
        if self._stop_event.wait(self.config.restart_delay):
            logger.info("Restart aborted by stop request")
            self._set_status(AppStatus.STOPPED)
            return False
        if self.start():
            return True
        # start() 有失败路径不置 ERROR（如端口被占），这里兜底，
        # 避免状态永久卡在 RESTARTING。
        if self.state.status != AppStatus.ERROR:
            self._set_status(AppStatus.ERROR, "Restart failed")
        return False

    def get_info(self) -> dict:
        """获取应用信息"""
        return {
            "status": self.state.status.value,
            "pid": self.state.pid,
            "version": self.state.version,
            "uptime_seconds": int(time.time() - self.state.start_time) if self.state.status == AppStatus.RUNNING else 0,
            "restart_count": self.state.restart_count,
            "last_error": self.state.last_error,
            "health_ok": self.state.health_ok,
            "backend_url": self.state.backend_url,
            "data_dir": str(self.config.data_dir),
            "log_dir": str(self.config.log_dir),
            "config": {
                "host": self.config.host,
                "port": self.config.port,
                "auto_restart": self.config.auto_restart,
                "max_restarts": self.config.max_restarts,
            },
        }

    def get_logs(self, lines: int = 100) -> str:
        """获取最近日志"""
        log_file = cast(Path, self.config.log_dir) / "backend.log"
        if not log_file.exists():
            return ""
        try:
            if lines <= 0:
                return ""
            with open(log_file, "rb") as f:
                f.seek(0, os.SEEK_END)
                position = f.tell()
                chunks = []
                newline_count = 0
                while position > 0 and newline_count <= lines:
                    read_size = min(8192, position)
                    position -= read_size
                    f.seek(position)
                    chunk = f.read(read_size)
                    chunks.append(chunk)
                    newline_count += chunk.count(b"\n")
                data = b"".join(reversed(chunks))
                text = b"\n".join(data.splitlines()[-lines:]).decode(
                    "utf-8", errors="ignore"
                )
                return text + ("\n" if text else "")
        except Exception:
            return ""

    def wait_for_shutdown(self):
        """等待关闭信号"""
        try:
            while self.state.status not in (AppStatus.STOPPED, AppStatus.ERROR):
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            self.stop()


# 全局单例
_runtime_manager: Optional[ApplicationRuntimeManager] = None


def get_runtime_manager(config: AppConfig | None = None) -> ApplicationRuntimeManager:
    global _runtime_manager
    if _runtime_manager is None:
        _runtime_manager = ApplicationRuntimeManager(config)
    return _runtime_manager
