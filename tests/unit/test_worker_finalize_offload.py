"""Worker 成功收尾卸载回归测试。

根因：质量评分会同步重开 Office 文档、修订派发会同步调用 LLM，
旧实现让优先级任务线程在整个收尾期间被占用。收尾现在统一提交到
独立的有界收尾执行器（task-finalize），任务函数返回即归还优先级线程。
"""
import threading
import time

import pytest

from office_agent.task_queue.worker import LocalWorker


def _make_worker(monkeypatch, normal_concurrency=1):
    from office_agent.task_queue.config import config
    queues = {name: dict(cfg) for name, cfg in config.TASK_QUEUES.items()}
    queues["normal"]["concurrency"] = normal_concurrency
    monkeypatch.setattr(config, "TASK_QUEUES", queues)
    worker = LocalWorker()
    worker._session_factory = None
    worker._update_status = lambda *_args, **_kwargs: None
    return worker


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_success_finalize_runs_on_finalize_thread(monkeypatch):
    """成功收尾（质量评分/落库）在 task-finalize 线程执行，不占任务线程。"""
    worker = _make_worker(monkeypatch)
    threads = {}
    finalize_done = threading.Event()

    def task_func(progress=None, **_kwargs):
        threads["task"] = threading.current_thread().name
        return {"status": "success", "output_files": []}

    def fake_complete(task_id, result, duration_ms=None):
        threads["finalize"] = threading.current_thread().name
        finalize_done.set()

    worker._complete = fake_complete
    worker.register("test.finalize-thread", task_func)
    try:
        worker.submit("test.finalize-thread")
        assert finalize_done.wait(3)
        assert threads["task"].startswith("task-")
        assert threads["finalize"].startswith("task-finalize")
        assert threads["task"] != threads["finalize"]
    finally:
        worker.shutdown()


def test_priority_thread_released_while_finalize_blocked(monkeypatch):
    """收尾阻塞期间，同优先级线程池必须能立刻执行下一个任务。"""
    worker = _make_worker(monkeypatch, normal_concurrency=1)
    finalize_entered = threading.Event()
    finalize_release = threading.Event()
    second_done = threading.Event()

    def first_task(progress=None, **_kwargs):
        return {"status": "success", "output_files": []}

    def second_task(progress=None, **_kwargs):
        second_done.set()
        return {"status": "success", "output_files": []}

    def blocking_complete(task_id, result, duration_ms=None):
        finalize_entered.set()
        finalize_release.wait(3)

    worker._complete = blocking_complete
    worker.register("test.blocked-a", first_task)
    worker.register("test.blocked-b", second_task)
    try:
        worker.submit("test.blocked-a")
        assert finalize_entered.wait(3)
        # 旧实现：收尾占用唯一的 normal 线程，第二个任务无法开始
        worker.submit("test.blocked-b")
        assert second_done.wait(3), "收尾阻塞期间优先级线程未被释放"
    finally:
        finalize_release.set()
        worker.shutdown()


def test_cancel_state_survives_until_finalize_done(monkeypatch):
    """收尾进行中取消事件/状态不得被主 Future 清理回调提前移除。"""
    worker = _make_worker(monkeypatch)
    finalize_entered = threading.Event()
    finalize_release = threading.Event()

    def task_func(progress=None, **_kwargs):
        return {"status": "success", "output_files": []}

    def blocking_complete(task_id, result, duration_ms=None):
        finalize_entered.set()
        finalize_release.wait(3)

    worker._complete = blocking_complete
    worker.register("test.cancel-state", task_func)
    try:
        task_id = worker.submit("test.cancel-state")
        assert finalize_entered.wait(3)
        # 主 Future 及其清理回调已结束（任务函数已返回）
        assert _wait_for(lambda: task_id not in worker._futures)
        # 竞态回归点：清理回调不得拔掉仍在使用的取消事件
        assert task_id in worker._cancel_events
        assert worker.get_active_count() == 1  # 收尾中仍计为活跃
        # 收尾期间取消：事件必须置位（修订派发据此放弃创建子任务）
        worker.revoke(task_id)
        assert worker._cancel_events[task_id].is_set()
        assert task_id in worker._cancelled
    finally:
        finalize_release.set()
        worker.shutdown()
    # 收尾完成后共享状态被收尾回调清理干净
    assert task_id not in worker._cancel_events
    assert task_id not in worker._cancelled
    assert worker.get_active_count() == 0


def test_finalize_executor_shutdown_falls_back_inline(monkeypatch):
    """进程退出竞态下收尾执行器拒绝提交时，内联回退且行为与旧版一致。"""
    worker = _make_worker(monkeypatch)
    threads = {}
    finalize_done = threading.Event()

    def task_func(progress=None, **_kwargs):
        threads["task"] = threading.current_thread().name
        return {"status": "success", "output_files": []}

    def fake_complete(task_id, result, duration_ms=None):
        threads["finalize"] = threading.current_thread().name
        finalize_done.set()

    def boom(*_args, **_kwargs):
        raise RuntimeError("cannot schedule new futures after shutdown")

    worker._complete = fake_complete
    worker._finalize_executor.submit = boom
    worker.register("test.inline-fallback", task_func)
    try:
        worker.submit("test.inline-fallback")
        assert finalize_done.wait(3)
        assert threads["finalize"] == threads["task"]
    finally:
        worker.shutdown()


def test_finalize_failure_marks_task_failed_without_escaping(monkeypatch):
    """收尾自身异常：走失败落库、不逃逸到执行器线程、活跃计数归零。"""
    worker = _make_worker(monkeypatch)
    failed = {}
    finalize_done = threading.Event()

    def task_func(progress=None, **_kwargs):
        return {"status": "success", "output_files": []}

    def boom_complete(task_id, result, duration_ms=None):
        raise ValueError("scoring blew up")

    def fake_fail(task_id, error, duration_ms=None):
        failed["task_id"] = task_id
        failed["error"] = error
        finalize_done.set()

    worker._complete = boom_complete
    worker._fail = fake_fail
    worker.register("test.finalize-error", task_func)
    try:
        task_id = worker.submit("test.finalize-error")
        assert finalize_done.wait(3)
        assert failed["task_id"] == task_id
        assert "scoring blew up" in failed["error"]
        # 收尾异常不得让任务永远占着活跃计数
        assert _wait_for(lambda: worker.get_active_count() == 0)
    finally:
        worker.shutdown()


def test_shutdown_stops_finalize_executor(monkeypatch):
    worker = _make_worker(monkeypatch)
    worker.shutdown()
    assert worker._finalize_executor._shutdown
    with pytest.raises(RuntimeError):
        worker._finalize_executor.submit(lambda: None)
