"""
Runtime Manager - 本地运行时管理器
负责启动/停止Backend，状态检测，自动重启
提供localhost API (127.0.0.1:8765)
"""
import os
import sys
import time
import signal
import socket
import subprocess
import threading
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass, field
from enum import Enum


class RuntimeStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"
    RESTARTING = "restarting"


@dataclass
class RuntimeConfig:
    """运行时配置"""
    host: str = "127.0.0.1"
    port: int = 8765
    workers: int = 1  # 本地模式单worker
    auto_restart: bool = True
    max_restarts: int = 5
    restart_delay: float = 2.0
    health_check_interval: float = 10.0
    startup_timeout: float = 30.0
    log_level: str = "info"
    project_root: str = ""
    python_path: str = ""


@dataclass
class RuntimeState:
    """运行时状态"""
    status: RuntimeStatus = RuntimeStatus.STOPPED
    pid: int = 0
    start_time: float = 0
    uptime_seconds: float = 0
    restart_count: int = 0
    last_error: str = ""
    last_health_check: float = 0
    health_ok: bool = False
    url: str = ""


class RuntimeManager:
    """
    本地运行时管理器
    - 启动Backend (uvicorn FastAPI)
    - 停止Backend
    - 健康检查
    - 异常自动重启
    - 进程监控
    """

    def __init__(self, config: RuntimeConfig = None):
        self._config = config or RuntimeConfig()
        self._state = RuntimeState(url=f"http://{self._config.host}:{self._config.port}")
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._callbacks: list[Callable] = []
        self._log_file = None
        self._lock = threading.RLock()

    @property
    def config(self) -> RuntimeConfig:
        return self._config

    @property
    def state(self) -> RuntimeState:
        return self._state

    def on_status_change(self, callback: Callable) -> None:
        """注册状态变化回调"""
        self._callbacks.append(callback)

    def _set_status(self, status: RuntimeStatus, error: str = "") -> None:
        old = self._state.status
        self._state.status = status
        if error:
            self._state.last_error = error
        if status == RuntimeStatus.RUNNING:
            self._state.start_time = time.time()
        elif status == RuntimeStatus.STOPPED:
            self._state.uptime_seconds = 0
        if old != status:
            for cb in self._callbacks:
                try:
                    cb(status, self._state)
                except Exception:
                    pass

    def is_port_in_use(self) -> bool:
        """检查端口是否被占用"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((self._config.host, self._config.port))
                return False
            except OSError:
                return True

    def check_health(self) -> bool:
        """健康检查"""
        try:
            import urllib.request
            url = f"http://{self._config.host}:{self._config.port}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                ok = resp.status == 200
                self._state.health_ok = ok
                self._state.last_health_check = time.time()
                return ok
        except Exception:
            self._state.health_ok = False
            return False

    def _build_command(self) -> list[str]:
        """构建启动命令"""
        python = self._config.python_path or sys.executable
        project_root = self._config.project_root or os.getcwd()
        cmd = [
            python, "-m", "uvicorn",
            "office_agent.api.main:app",
            "--host", self._config.host,
            "--port", str(self._config.port),
            "--log-level", self._config.log_level,
        ]
        if self._config.workers > 1:
            cmd.extend(["--workers", str(self._config.workers)])
        return cmd

    def start(self) -> bool:
        """启动Backend"""
        with self._lock:
            if self._state.status in (RuntimeStatus.RUNNING, RuntimeStatus.STARTING):
                return True
            if self.is_port_in_use():
                # 检查是否是我们的服务
                if self.check_health():
                    self._set_status(RuntimeStatus.RUNNING)
                    return True
                self._set_status(RuntimeStatus.ERROR, f"端口 {self._config.port} 被占用")
                return False
            self._set_status(RuntimeStatus.STARTING)
            self._stop_event.clear()
            try:
                project_root = self._config.project_root or os.getcwd()
                # 日志文件
                log_dir = Path(project_root) / "logs"
                log_dir.mkdir(exist_ok=True)
                self._log_file = open(log_dir / "backend.log", "a", encoding="utf-8")
                self._log_file.write(f"\n{'='*50}\n{time.ctime()} - Starting backend\n{'='*50}\n")
                self._log_file.flush()
                # 启动进程
                env = os.environ.copy()
                env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
                env["OFFICE_AGENT_LOCAL"] = "1"
                cmd = self._build_command()
                self._process = subprocess.Popen(
                    cmd,
                    cwd=project_root,
                    env=env,
                    stdout=self._log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self._state.pid = self._process.pid
                self._state.restart_count = 0
                # 等待启动
                start_time = time.time()
                while time.time() - start_time < self._config.startup_timeout:
                    if self._process.poll() is not None:
                        self._set_status(RuntimeStatus.ERROR, f"进程退出，代码: {self._process.returncode}")
                        return False
                    if self.check_health():
                        self._set_status(RuntimeStatus.RUNNING)
                        # 启动监控线程
                        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
                        self._monitor_thread.start()
                        return True
                    time.sleep(0.5)
                # 超时
                self.stop()
                self._set_status(RuntimeStatus.ERROR, "启动超时")
                return False
            except Exception as e:
                self._set_status(RuntimeStatus.ERROR, str(e))
                return False

    def stop(self, timeout: float = 10.0) -> bool:
        """停止Backend"""
        with self._lock:
            self._stop_event.set()
            if self._process:
                try:
                    if os.name == "nt":
                        self._process.terminate()
                    else:
                        self._process.send_signal(signal.SIGTERM)
                    try:
                        self._process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=5)
                except Exception:
                    pass
                self._process = None
            if self._log_file:
                try:
                    self._log_file.close()
                except Exception:
                    pass
                self._log_file = None
            self._state.pid = 0
            self._set_status(RuntimeStatus.STOPPED)
            return True

    def restart(self) -> bool:
        """重启Backend"""
        self._set_status(RuntimeStatus.RESTARTING)
        self.stop()
        time.sleep(1)
        return self.start()

    def _monitor_loop(self) -> None:
        """监控循环"""
        while not self._stop_event.is_set():
            time.sleep(self._config.health_check_interval)
            if self._stop_event.is_set():
                break
            # 检查进程是否存活
            if self._process and self._process.poll() is not None:
                exit_code = self._process.returncode
                if self._config.auto_restart and self._state.restart_count < self._config.max_restarts:
                    self._state.restart_count += 1
                    self._set_status(RuntimeStatus.RESTARTING, f"进程退出(代码{exit_code})，自动重启...")
                    time.sleep(self._config.restart_delay)
                    if not self._stop_event.is_set():
                        self._do_restart()
                else:
                    self._set_status(RuntimeStatus.ERROR, f"进程退出(代码{exit_code})，已达最大重启次数")
                    break
            else:
                # 健康检查
                if not self.check_health():
                    if self._config.auto_restart:
                        self._set_status(RuntimeStatus.RESTARTING, "健康检查失败，重启中...")
                        time.sleep(self._config.restart_delay)
                        if not self._stop_event.is_set():
                            self._do_restart()
                else:
                    self._state.uptime_seconds = time.time() - self._state.start_time

    def _do_restart(self) -> None:
        """内部重启"""
        try:
            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    if self._process:
                        self._process.kill()
            self._process = None
            # 重新启动
            project_root = self._config.project_root or os.getcwd()
            log_dir = Path(project_root) / "logs"
            log_dir.mkdir(exist_ok=True)
            self._log_file = open(log_dir / "backend.log", "a", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
            env["OFFICE_AGENT_LOCAL"] = "1"
            self._process = subprocess.Popen(
                self._build_command(),
                cwd=project_root,
                env=env,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._state.pid = self._process.pid
            # 等待启动
            start_time = time.time()
            while time.time() - start_time < self._config.startup_timeout:
                if self._process.poll() is not None:
                    break
                if self.check_health():
                    self._set_status(RuntimeStatus.RUNNING)
                    return
                time.sleep(0.5)
            self._set_status(RuntimeStatus.ERROR, "重启失败")
        except Exception as e:
            self._set_status(RuntimeStatus.ERROR, str(e))

    def get_logs(self, lines: int = 100) -> str:
        """获取日志"""
        project_root = self._config.project_root or os.getcwd()
        log_path = Path(project_root) / "logs" / "backend.log"
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                    return "".join(all_lines[-lines:])
            except Exception:
                return ""
        return ""

    def get_info(self) -> dict:
        """获取运行时信息"""
        return {
            "status": self._state.status.value,
            "pid": self._state.pid,
            "url": self._state.url,
            "uptime": int(self._state.uptime_seconds),
            "restart_count": self._state.restart_count,
            "health_ok": self._state.health_ok,
            "last_error": self._state.last_error,
            "config": {
                "host": self._config.host,
                "port": self._config.port,
                "workers": self._config.workers,
                "auto_restart": self._config.auto_restart,
            },
        }


# 全局实例
_runtime: Optional[RuntimeManager] = None


def get_runtime(config: RuntimeConfig = None) -> RuntimeManager:
    global _runtime
    if _runtime is None:
        _runtime = RuntimeManager(config)
    return _runtime
