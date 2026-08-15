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
from dataclasses import dataclass, field
from typing import Optional, Callable
import urllib.request
import urllib.error

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
    app_version: str = "0.49.0"
    host: str = "127.0.0.1"
    port: int = 8765
    backend_module: str = "office_agent.api.main:app"
    auto_start_backend: bool = True
    auto_restart: bool = True
    max_restarts: int = 5
    restart_delay: float = 2.0
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
            if sys.platform == "win32":
                base = Path(os.environ.get("APPDATA", Path.home())) / "OfficeAgent"
            elif sys.platform == "darwin":
                base = Path.home() / "Library" / "Application Support" / "OfficeAgent"
            else:
                base = Path.home() / ".local" / "share" / "OfficeAgent"
            self.data_dir = base
        if self.log_dir is None:
            self.log_dir = self.data_dir / "logs"
        if self.backend_dir is None:
            self.backend_dir = Path(__file__).parent.parent.parent
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

    def __init__(self, config: AppConfig = None):
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
                pass
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
                    self.state.health_ok = data.get("status") == "ok"
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
        cmd = [
            self.config.python_executable,
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
        env["OFFICE_AGENT_LOCAL"] = "1"
        env["AUTH_MODE"] = "local"
        env["OFFICE_AGENT_DATA_DIR"] = str(self.config.data_dir)
        env["OFFICE_AGENT_LOG_DIR"] = str(self.config.log_dir)
        env["OFFICE_AGENT_VERSION"] = self.config.app_version
        # Python路径
        backend_dir = str(self.config.backend_dir)
        if backend_dir not in env.get("PYTHONPATH", ""):
            env["PYTHONPATH"] = backend_dir + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def start(self) -> bool:
        """启动Backend"""
        if self.state.status == AppStatus.RUNNING:
            logger.info("Already running")
            return True
        if self.is_port_in_use():
            # 端口被占用，可能已经在运行
            if self.check_health():
                self._set_status(AppStatus.RUNNING)
                return True
            logger.warning(f"Port {self.config.port} in use but health check failed")
            return False
        self._set_status(AppStatus.STARTING)
        try:
            cmd = self._build_command()
            env = self._get_env()
            log_file = self.config.log_dir / "backend.log"
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
                time.sleep(0.5)
            self._set_status(AppStatus.ERROR, "Startup timeout")
            self._kill_process()
            return False
        except Exception as e:
            self._set_status(AppStatus.ERROR, str(e))
            return False

    def stop(self, timeout: float = None) -> bool:
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
        time.sleep(self.config.restart_delay)
        return self.start()

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

    def _do_restart(self):
        """执行重启"""
        self._kill_process()
        self._process = None
        time.sleep(self.config.restart_delay)
        self.start()

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
        log_file = self.config.log_dir / "backend.log"
        if not log_file.exists():
            return ""
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
                return "".join(all_lines[-lines:])
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


def get_runtime_manager(config: AppConfig = None) -> ApplicationRuntimeManager:
    global _runtime_manager
    if _runtime_manager is None:
        _runtime_manager = ApplicationRuntimeManager(config)
    return _runtime_manager
