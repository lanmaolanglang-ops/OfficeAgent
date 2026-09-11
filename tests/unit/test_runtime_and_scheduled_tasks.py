"""High-value lifecycle coverage for the desktop runtime and scheduled jobs."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta

import pytest


class _Progress:
    def __init__(self):
        self.updates: list[tuple[int, str]] = []

    def update(self, value: int, message: str):
        self.updates.append((value, message))


class _ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target
        self.started = False

    def start(self):
        self.started = True
        self.target()

    def join(self, timeout=None):
        return timeout


def test_scheduler_crud_execution_fallback_and_loop(monkeypatch):
    import office_agent.task_queue as task_queue
    from office_agent.task_queue import scheduler as scheduler_module

    calls = []
    sched = scheduler_module.TaskScheduler()
    sched.add("job", lambda value=0: calls.append(value), 60, args=(7,))
    listed = sched.list_tasks()
    assert listed[0]["name"] == "job"
    assert listed[0]["run_count"] == 0

    monkeypatch.setattr(task_queue, "init_worker", lambda: None)
    monkeypatch.setattr(scheduler_module.threading, "Thread", _ImmediateThread)
    task = sched._tasks["job"]
    sched._execute(task)
    assert calls == [7]
    assert task.run_count == 1
    assert task.last_run is not None

    monkeypatch.setattr(task_queue, "init_worker", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    sched._execute(task)
    assert calls == [7, 7]

    task.next_run = datetime.now() - timedelta(seconds=1)
    sched._running = True
    monkeypatch.setattr(scheduler_module.time, "sleep", lambda _seconds: setattr(sched, "_running", False))
    sched._run_loop()
    assert task.run_count == 3

    sched.remove("job")
    assert sched.list_tasks() == []


def test_scheduler_start_stop_and_default_registration(monkeypatch):
    from office_agent.task_queue import scheduler as scheduler_module

    sched = scheduler_module.TaskScheduler()
    monkeypatch.setattr(scheduler_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(sched, "_run_loop", lambda: None)
    sched.start()
    assert sched._running is True
    sched.start()  # idempotent
    sched.stop()
    assert sched._running is False

    monkeypatch.setenv("LOG_RETENTION_DAYS", "14")
    registered = scheduler_module.TaskScheduler()
    scheduler_module.setup_default_schedules(registered)
    tasks = {item["name"]: item for item in registered.list_tasks()}
    assert set(tasks) == {
        "system_health_check", "cleanup_temp_files",
        "refresh_knowledge_base", "cleanup_old_logs",
    }
    assert registered._tasks["cleanup_old_logs"].kwargs == {"days": 14}


def test_file_processing_metadata_and_failure_paths(sample_docx, sample_xlsx, sample_pptx, tmp_path):
    import pymupdf
    from office_agent.task_queue.tasks.file_tasks import convert_format, process_upload

    progress = _Progress()
    word = process_upload(str(sample_docx), "word-id", progress=progress)
    excel = process_upload(str(sample_xlsx), "excel-id")
    slides = process_upload(str(sample_pptx), "slides-id")
    pdf_path = tmp_path / "sample.pdf"
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.save(pdf_path)
    pdf.close()
    document = process_upload(str(pdf_path), "pdf-id")

    assert word["metadata"]["paragraphs"] >= 1
    assert excel["metadata"]["sheets"] == ["\u9500\u552e\u6570\u636e", "\u5e93\u5b58"]
    assert slides["metadata"]["slides"] == 2
    assert document["metadata"]["pages"] == 1
    assert progress.updates[-1][0] == 100

    with pytest.raises(FileNotFoundError):
        process_upload(str(tmp_path / "missing.docx"), "missing")
    with pytest.raises(RuntimeError, match="\u6682\u672a\u5b9e\u73b0"):
        convert_format(str(sample_docx), str(tmp_path / "out.pdf"), "pdf")
    with pytest.raises(FileNotFoundError):
        convert_format(str(tmp_path / "missing.docx"), str(tmp_path / "out.pdf"), "pdf")


def test_temp_cleanup_and_health_check(tmp_path, monkeypatch):
    from office_agent.task_queue.tasks import file_tasks

    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path))
    old_time = (datetime.now() - timedelta(days=8)).timestamp()
    expected_freed = 0
    for relative in (
        "outputs/old.bin",
        "storage/multipart/upload/part-1",
        "storage/files/document.tmp-orphan",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = relative.encode()
        path.write_bytes(payload)
        expected_freed += len(payload)
        os.utime(path, (old_time, old_time))
    fresh = tmp_path / "temp/fresh.bin"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_bytes(b"keep")

    progress = _Progress()
    result = file_tasks.cleanup_temp_files(progress=progress)
    assert result == {"status": "success", "cleaned": 3, "freed_bytes": expected_freed}
    assert fresh.exists()
    assert progress.updates[-1][0] == 100

    class _Repo:
        def __init__(self, _session):
            pass

        def count(self):
            return 12

        def get_pending_tasks(self, limit):
            assert limit == 100
            return [1, 2]

    class _Scope:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    import office_agent.database.repository as repository_module
    import office_agent.database.session as session_module
    monkeypatch.setattr(repository_module, "TaskRepository", _Repo)
    monkeypatch.setattr(session_module, "session_scope", lambda: _Scope())
    health = file_tasks.system_health_check(progress=progress)
    assert health["status"] == "success"
    assert health["checks"]["total_tasks"] == 12
    assert health["checks"]["pending_tasks"] == 2
    assert "timestamp" in health["checks"]


def _runtime(tmp_path, **overrides):
    from office_agent.runtime_manager import AppConfig, ApplicationRuntimeManager

    values = {
        "data_dir": tmp_path / "data",
        "log_dir": tmp_path / "logs",
        "backend_dir": tmp_path,
        "python_executable": "python-test",
        "port": 18766,
        "restart_delay": 0,
        "startup_timeout": 1,
        "health_check_interval": 0,
    }
    values.update(overrides)
    return ApplicationRuntimeManager(AppConfig(**values))


def test_runtime_configuration_status_info_and_logs(tmp_path, monkeypatch):
    from office_agent.runtime_manager import AppStatus

    manager = _runtime(tmp_path)
    assert (tmp_path / "data/config").is_dir()
    assert manager._build_command()[-4:] == ["--port", "18766", "--log-level", "info"]
    env = manager._get_env()
    assert env["AUTH_MODE"] == "local"
    assert env["OFFICE_AGENT_DATA_DIR"] == str(tmp_path / "data")
    assert str(tmp_path) in env["PYTHONPATH"]

    changes = []
    manager.on_status_change(lambda status, state: changes.append((status, state.status)))
    manager.on_status_change(lambda *_args: (_ for _ in ()).throw(RuntimeError("callback")))
    monkeypatch.setattr("office_agent.runtime_manager.time.time", lambda: 100.0)
    manager._set_status(AppStatus.RUNNING)
    manager.state.start_time = 90
    assert manager.get_info()["uptime_seconds"] == 10
    manager._set_status(AppStatus.STOPPED)
    assert manager.state.uptime_seconds == 10
    assert changes[0] == (AppStatus.RUNNING, AppStatus.RUNNING)

    assert manager.get_logs() == ""
    (tmp_path / "logs/backend.log").write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert manager.get_logs(2) == "two\nthree\n"


def test_runtime_port_and_health_checks(tmp_path, monkeypatch):
    manager = _runtime(tmp_path, port=0)
    assert manager.is_port_in_use() is False

    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    port = occupied.getsockname()[1]
    try:
        manager.config.port = port
        assert manager.is_port_in_use() is True
    finally:
        occupied.close()

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"status": "degraded"}).encode()

    monkeypatch.setattr("office_agent.runtime_manager.urllib.request.urlopen", lambda *_args, **_kwargs: _Response())
    assert manager.check_health() is True
    monkeypatch.setattr("office_agent.runtime_manager.urllib.request.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")))
    assert manager.check_health() is False


class _FakeProcess:
    def __init__(self, pid=4321, poll_values=None):
        self.pid = pid
        self.returncode = None
        self._poll_values = iter(poll_values or [None])
        self.terminated = False
        self.killed = False

    def poll(self):
        try:
            value = next(self._poll_values)
        except StopIteration:
            value = None
        if value is not None:
            self.returncode = value
        return value

    def terminate(self):
        self.terminated = True

    def send_signal(self, _signal):
        self.terminated = True

    def wait(self, timeout=None):
        return timeout

    def kill(self):
        self.killed = True


def test_runtime_start_success_existing_service_and_failures(tmp_path, monkeypatch):
    from office_agent.runtime_manager import AppStatus
    import office_agent.runtime_manager as runtime_module

    manager = _runtime(tmp_path)
    process = _FakeProcess()
    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "check_health", lambda: True)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(runtime_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(manager, "_monitor_loop", lambda: None)
    assert manager.start() is True
    assert manager.state.status == AppStatus.RUNNING
    assert manager.state.pid == 4321
    assert manager.start() is True

    existing = _runtime(tmp_path / "existing")
    monkeypatch.setattr(existing, "is_port_in_use", lambda: True)
    monkeypatch.setattr(existing, "check_health", lambda: True)
    assert existing.start() is True
    unhealthy = _runtime(tmp_path / "unhealthy")
    monkeypatch.setattr(unhealthy, "is_port_in_use", lambda: True)
    monkeypatch.setattr(unhealthy, "check_health", lambda: False)
    assert unhealthy.start() is False

    exited = _runtime(tmp_path / "exited")
    monkeypatch.setattr(exited, "is_port_in_use", lambda: False)
    monkeypatch.setattr(exited, "check_health", lambda: False)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *_args, **_kwargs: _FakeProcess(poll_values=[9]))
    assert exited.start() is False
    assert "code 9" in exited.state.last_error

    broken = _runtime(tmp_path / "broken")
    monkeypatch.setattr(broken, "is_port_in_use", lambda: False)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")))
    assert broken.start() is False
    assert broken.state.status == AppStatus.ERROR


def test_runtime_start_returns_at_first_ready_not_after_fixed_wait(tmp_path, monkeypatch):
    """L449 证据：readiness 等待在首次健康检查通过时立即返回，
    等待时长由健康检查节奏决定，并非固定占用调用线程 30 秒。"""
    from office_agent.runtime_manager import AppStatus
    import office_agent.runtime_manager as runtime_module

    manager = _runtime(tmp_path / "delay", startup_timeout=30)
    process = _FakeProcess()
    health_calls = []

    def _health():
        health_calls.append(1)
        return len(health_calls) >= 3

    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "check_health", _health)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(runtime_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(manager, "_monitor_loop", lambda: None)
    ticks = []
    real_wait = manager._stop_event.wait

    def _tick(seconds=0.5):
        ticks.append(seconds)
        return real_wait(0)

    monkeypatch.setattr(manager, "_startup_wait_tick", _tick)
    assert manager.start() is True
    assert manager.state.status == AppStatus.RUNNING
    assert manager.state.pid == 4321
    # 第 3 次健康检查通过即返回：只有 2 次 0.5s 间隔，远低于 30s 上限
    assert len(health_calls) == 3
    assert ticks == [0.5, 0.5]


def test_runtime_start_timeout_kills_child_process(tmp_path, monkeypatch):
    """L449 证据：等待有界（startup_timeout），超时后 kill 子进程，不留孤儿。"""
    from office_agent.runtime_manager import AppStatus
    import office_agent.runtime_manager as runtime_module

    manager = _runtime(tmp_path / "timeout")  # startup_timeout=1
    process = _FakeProcess()
    ticks = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(runtime_module.time, "time", lambda: next(ticks, 100.0))
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "check_health", lambda: False)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    assert manager.start() is False
    assert manager.state.status == AppStatus.ERROR
    assert "Startup timeout" in manager.state.last_error
    # 超时清理：子进程被 kill 且 wait 回收，不产生孤儿进程
    assert process.killed is True


def test_runtime_start_stop_during_startup_terminates_child(tmp_path, monkeypatch):
    """L449 证据：startup 轮询期间调用 stop()（Windows 服务 SvcStop 语义），
    子进程被 terminate+wait 回收，start() 明确失败返回，不产生孤儿。"""
    import threading as real_threading

    import office_agent.runtime_manager as runtime_module

    manager = _runtime(tmp_path / "stop-start", startup_timeout=30)
    process = _FakeProcess()
    started_polling = real_threading.Event()
    allow_health_return = real_threading.Event()

    def _health():
        started_polling.set()
        allow_health_return.wait(5)
        return False

    monkeypatch.setattr(manager, "is_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "check_health", _health)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", lambda *_args, **_kwargs: process)

    result = []
    starter = real_threading.Thread(target=lambda: result.append(manager.start()))
    starter.start()
    assert started_polling.wait(5)
    # 模拟 SCM 在 startup 轮询期间并发调用 stop()
    assert manager.stop() is True
    allow_health_return.set()
    starter.join(5)
    assert not starter.is_alive()
    assert result == [False]
    assert process.terminated is True


def test_runtime_stop_restart_kill_monitor_and_singleton(tmp_path, monkeypatch):
    from office_agent.runtime_manager import AppStatus
    import office_agent.runtime_manager as runtime_module

    manager = _runtime(tmp_path)
    assert manager.stop() is True
    process = _FakeProcess()
    manager._process = process
    manager.state.status = AppStatus.RUNNING
    assert manager.stop() is True
    assert process.terminated is True
    assert manager._process is None

    manager._process = _FakeProcess()
    manager._kill_process()
    assert manager._process.killed is True
    monkeypatch.setattr(manager, "stop", lambda: True)
    monkeypatch.setattr(manager, "start", lambda: True)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    assert manager.restart() is True

    crashed = _runtime(tmp_path / "crashed", auto_restart=False)
    crashed._process = _FakeProcess(poll_values=[3])
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    crashed._monitor_loop()
    assert crashed.state.status == AppStatus.ERROR

    restarting = _runtime(tmp_path / "restarting")
    restarting._process = _FakeProcess(poll_values=[2])
    monkeypatch.setattr(restarting, "_do_restart", lambda: restarting._stop_event.set())
    restarting._monitor_loop()
    assert restarting.state.restart_count == 1

    runtime_module._runtime_manager = None
    singleton = runtime_module.get_runtime_manager(manager.config)
    assert runtime_module.get_runtime_manager() is singleton
    runtime_module._runtime_manager = None
