"""P1-3 回归测试：健康失败重启必须是"真重启"，不能是 no-op。

历史缺陷链：
    健康失败 -> _do_restart() -> _kill_process(); _process=None; start()
    -> start() 看到 status 仍是 RUNNING -> return True（空转）
结果旧进程已 kill、新进程从未创建、旧监控线程 break 后无新线程接替，
外部却认为重启成功。

本文件所有用例均 mock process / health / port / 线程，不启动真实后端。
"""
import json
import socket

import pytest

from office_agent.runtime_manager import AppConfig, AppStatus
import office_agent.runtime_manager as runtime_module


class _FakeProcess:
    def __init__(self, pid, poll_values=None):
        self.pid = pid
        self.returncode = None
        self._poll_values = iter(poll_values or [])
        self.killed = False
        self.terminated = False

    def poll(self):
        try:
            value = next(self._poll_values)
        except StopIteration:
            value = None
        if value is not None:
            self.returncode = value
        return value

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True

    def send_signal(self, _sig):
        self.terminated = True

    def wait(self, timeout=None):
        return timeout


class _ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target
        self.started = False

    def start(self):
        self.started = True
        self.target()

    def join(self, timeout=None):
        return timeout


class _DeferredThread:
    """只记录"线程已启动"而不真正执行 target，避免监控循环递归。"""

    def __init__(self, target, **_kwargs):
        self.target = target
        self.started = False

    def start(self):
        self.started = True

    def join(self, timeout=None):
        return timeout


class _StubEvent:
    """可控的 stop event：wait_result=True 模拟"等待途中收到停止请求"。"""

    def __init__(self, wait_result=False):
        self._wait_result = wait_result
        self._set = False
        self.clear_count = 0

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def clear(self):
        self._set = False
        self.clear_count += 1

    def wait(self, timeout=None):
        return self._wait_result


def _manager(tmp_path, **overrides):
    values = {
        "data_dir": tmp_path / "data",
        "log_dir": tmp_path / "logs",
        "backend_dir": tmp_path,
        "python_executable": "python-test",
        "port": 18801,
        "restart_delay": 0,
        "startup_timeout": 5,
        "health_check_interval": 0,
    }
    values.update(overrides)
    return runtime_module.ApplicationRuntimeManager(AppConfig(**values))


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_health_failure_restart_spawns_a_new_process(tmp_path, monkeypatch):
    """健康连续失败后必须真正创建第二个子进程，且监控线程重新建立。"""
    manager = _manager(tmp_path, port=_free_port())
    first = _FakeProcess(1001)
    second = _FakeProcess(1002)
    spawned = []

    def _popen(*_args, **_kwargs):
        spawned.append(1)
        return first if len(spawned) == 1 else second

    monkeypatch.setattr(runtime_module.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", _popen)
    monkeypatch.setattr(runtime_module.threading, "Thread", _DeferredThread)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "check_health", lambda: True)

    # 1) 正常启动：第一个进程由 start() 创建
    assert manager.start() is True
    assert manager._process is first
    assert manager.state.status == AppStatus.RUNNING
    monitor_before = manager._monitor_thread

    # 2) 进程存活但健康检查连续失败；第二个进程拉起后健康恢复
    def _health():
        return len(spawned) >= 2

    monkeypatch.setattr(manager, "check_health", _health)
    manager._monitor_loop()

    # 核心断言：确实创建了第二个进程，而不是空转返回
    assert len(spawned) == 2
    assert first.killed is True
    assert manager._process is second
    assert manager._process is not first
    assert manager.state.pid == 1002
    assert manager.state.status == AppStatus.RUNNING
    assert manager.state.restart_count == 1
    # 监控线程必须被重新建立，否则后续再无看护
    assert manager._monitor_thread is not None
    assert manager._monitor_thread is not monitor_before
    assert manager._monitor_thread.started is True


def test_restart_state_passes_through_restarting(tmp_path, monkeypatch):
    """重启过程中状态必须经历 RESTARTING，kill 旧进程时不能仍是 RUNNING。"""
    manager = _manager(tmp_path)
    first = _FakeProcess(1001)
    second = _FakeProcess(1002)
    spawned = []
    seen = []

    def _popen(*_args, **_kwargs):
        spawned.append(1)
        return second if len(spawned) > 1 else first

    monkeypatch.setattr(runtime_module.subprocess, "Popen", _popen)
    monkeypatch.setattr(runtime_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(manager, "_monitor_loop", lambda: None)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "check_health", lambda: True)
    manager._process = first
    manager.state.status = AppStatus.RUNNING
    manager.on_status_change(lambda status, _state: seen.append(status))

    assert manager._do_restart() is True
    assert AppStatus.RESTARTING in seen
    assert seen[-1] == AppStatus.RUNNING
    assert first.killed is True


def test_do_restart_failure_lands_in_error_not_restarting(tmp_path, monkeypatch):
    """新进程起不来时状态必须落到 ERROR，不能永久卡在 RESTARTING。"""
    manager = _manager(tmp_path)
    manager._process = _FakeProcess(1001)
    manager.state.status = AppStatus.RUNNING
    monkeypatch.setattr(manager, "start", lambda: False)  # 启动失败且不改状态
    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)

    assert manager._do_restart() is False
    assert manager.state.status == AppStatus.ERROR
    assert manager.state.last_error == "Restart failed"


def test_do_restart_propagates_start_failure(tmp_path, monkeypatch):
    """restart() 必须把启动失败传播给调用方，不能无条件返回 True。"""
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "start", lambda: False)
    monkeypatch.setattr(manager, "stop", lambda: True)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)
    assert manager.restart() is False
    assert manager.state.status == AppStatus.ERROR


def test_start_reports_error_when_port_occupied_and_unhealthy(tmp_path, monkeypatch):
    """端口被占且占用者不健康：不能维持 RUNNING 假象。"""
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    port = occupied.getsockname()[1]
    try:
        manager = _manager(tmp_path / "occupied", port=port)
        manager.state.status = AppStatus.RUNNING
        monkeypatch.setattr(manager, "check_health", lambda: False)
        assert manager.start() is False
        assert manager.state.status == AppStatus.ERROR
        assert "occupied" in manager.state.last_error
    finally:
        occupied.close()


def test_start_aborts_immediately_when_stop_requested(tmp_path, monkeypatch):
    """_stop_event 必须能立即中断启动等待，而不是继续睡满 0.5s 节拍。"""
    manager = _manager(tmp_path)
    process = _FakeProcess(1001)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "check_health", lambda: False)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *_a, **_k: process)

    manager._stop_event.set()
    assert manager.start() is False
    # 启动被放弃：子进程被 kill 回收，状态 STOPPED
    assert process.killed is True
    assert manager._process is None
    assert manager.state.status == AppStatus.STOPPED


def test_startup_wait_tick_is_interruptible_by_stop_event(tmp_path, monkeypatch):
    """启动等待节拍由 _stop_event 驱动，stop() 可立即唤醒。"""
    manager = _manager(tmp_path)
    sleeps = []
    monkeypatch.setattr(runtime_module.time, "sleep",
                        lambda seconds: sleeps.append(seconds))
    manager._stop_event.set()
    assert manager._startup_wait_tick() is True
    assert sleeps == []  # 没有无意义的 time.sleep


def test_do_restart_aborted_when_stop_requested_during_delay(tmp_path, monkeypatch):
    """重启等待期间收到停止请求：不再拉起新进程。"""
    manager = _manager(tmp_path)
    manager._process = _FakeProcess(1001)
    manager.state.status = AppStatus.RUNNING
    manager._stop_event = _StubEvent(wait_result=True)
    monkeypatch.setattr(manager, "start", lambda: pytest.fail("不应启动新进程"))

    assert manager._do_restart() is False
    assert manager.state.status == AppStatus.STOPPED


def test_do_restart_clears_stale_stop_event_before_start(tmp_path, monkeypatch):
    """stop() 留下的 _stop_event 必须在重启前清掉，否则 start() 会立刻放弃。"""
    manager = _manager(tmp_path)
    second = _FakeProcess(1002)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *_a, **_k: second)
    monkeypatch.setattr(runtime_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(manager, "_monitor_loop", lambda: None)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "check_health", lambda: True)

    manager._process = _FakeProcess(1001)
    manager.state.status = AppStatus.RUNNING
    manager._stop_event.set()  # stop() 遗留

    assert manager._do_restart() is True
    assert manager._stop_event.is_set() is False
    assert manager.state.status == AppStatus.RUNNING


def test_monitor_loop_health_recovery_does_not_restart(tmp_path, monkeypatch):
    """健康恢复后不得误触发重启（防止修复引入过度重启）。"""
    manager = _manager(tmp_path)
    process = _FakeProcess(1001)
    manager._process = process
    manager.state.status = AppStatus.RUNNING
    manager.state.start_time = 0
    monkeypatch.setattr(manager, "check_health", lambda: True)

    calls = {"n": 0}

    def _counting_health():
        calls["n"] += 1
        return True

    monkeypatch.setattr(manager, "check_health", _counting_health)
    monkeypatch.setattr(manager, "_do_restart",
                        lambda: pytest.fail("健康正常不应重启"))
    # 手动跑 5 轮：进程存活 + 健康，不应触发重启
    for _ in range(5):
        if manager._process.poll() is not None:
            break
        if not manager.check_health():
            manager._health_failures += 1
            if manager._health_failures >= manager._max_health_failures:
                manager._do_restart()
                break
        else:
            manager._health_failures = 0
    assert manager.state.restart_count == 0
    assert process.killed is False


def test_check_health_accepts_degraded_status(tmp_path, monkeypatch):
    """/health 返回 degraded 仍视为可用（既有语义不得被破坏）。"""

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"status": "degraded"}).encode()

    manager = _manager(tmp_path)
    monkeypatch.setattr(runtime_module.urllib.request, "urlopen",
                        lambda *_a, **_k: _Response())
    assert manager.check_health() is True
    assert manager.state.health_ok is True
