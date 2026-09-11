"""P1-6 回归测试：取消已终态任务不得回复"任务已取消"。

历史缺陷：
    ``TaskRepository.cancel_task`` 的 SQL 带
    ``WHERE status IN ("pending", "queued", "running")``，
    返回 ``bool(rowcount)``。因此 ``False`` 是二义的：
    * 任务不存在
    * 或任务已处于终态（success / failed / cancelled）

    而 ``_cancel_task_impl`` 里 ``if repo.cancel_task(...)`` **没有 else 分支**，
    无论返回值都 ``return BaseResponse(message="任务已取消")``——
    取消一个早已完成的任务也报成功。

修复：
    返回值 False 且任务确实存在时，抛出既有的 ``TaskStateError``（409）。
    任务不存在仍走 ``TaskNotFoundError``（404），与历史一致。

本文件全部使用 mock：不连接真实数据库。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _Session:
    def __init__(self):
        self.closed = False
        self.commits = 0

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def _install(monkeypatch, tasks, session=None):
    """装配 fake repository；tasks 为 {task_id: SimpleNamespace(status=...)}。"""
    from office_agent.api.router import task as task_router
    import office_agent.database.repository as repository_module
    import office_agent.task_queue as queue_module

    class _TaskRepo:
        def __init__(self, _session):
            pass

        def get_by_id(self, task_id):
            return tasks.get(task_id)

        def cancel_task(self, task_id):
            """复刻真实实现：仅活动状态可取消，返回 bool(rowcount)。"""
            task = tasks.get(task_id)
            if task is None:
                return False
            if task.status not in ("pending", "queued", "running"):
                return False
            task.status = "cancelled"
            return True

    monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
    monkeypatch.setattr(task_router, "_get_db_session",
                        lambda: session if session is not None else _Session())
    monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)
    original = task_router.task_manager.tasks
    task_router.task_manager.tasks = {}
    return task_router, original


def _run(monkeypatch, status, task_id="t-1"):
    tasks = {task_id: SimpleNamespace(id=task_id, status=status)}
    task_router, original = _install(monkeypatch, tasks)
    try:
        return asyncio.run(task_router._cancel_task_impl(task_id))
    finally:
        task_router.task_manager.tasks = original


# ------------------------------------------------------------ 可取消：成功


@pytest.mark.parametrize("status", ["pending", "queued", "running"])
def test_cancellable_status_reports_success(monkeypatch, status):
    resp = _run(monkeypatch, status)
    assert resp.message == "任务已取消"


def test_cancellable_status_actually_transitions(monkeypatch):
    from office_agent.api.router import task as task_router
    import office_agent.database.repository as repository_module
    import office_agent.task_queue as queue_module
    from types import SimpleNamespace

    task = SimpleNamespace(id="t-1", status="running")
    monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)

    class _TaskRepo:
        def __init__(self, _session):
            pass

        def get_by_id(self, task_id):
            return task

        def cancel_task(self, task_id):
            if task.status not in ("pending", "queued", "running"):
                return False
            task.status = "cancelled"
            return True

    session = _Session()
    monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
    monkeypatch.setattr(task_router, "_get_db_session", lambda: session)
    original = task_router.task_manager.tasks
    task_router.task_manager.tasks = {}
    try:
        resp = asyncio.run(task_router._cancel_task_impl("t-1"))
    finally:
        task_router.task_manager.tasks = original

    assert resp.message == "任务已取消"
    assert task.status == "cancelled"
    assert session.commits == 1


# ------------------------------------------------- 已终态：不得再报成功


@pytest.mark.parametrize("status", ["success", "failed", "cancelled"])
def test_terminal_status_does_not_report_success(monkeypatch, status):
    from office_agent.api.core.exceptions import TaskStateError

    with pytest.raises(TaskStateError) as excinfo:
        _run(monkeypatch, status)

    assert "终态" in str(excinfo.value)
    assert status in str(excinfo.value)


def test_terminal_status_maps_to_http_409(monkeypatch):
    """终态冲突必须是 409，不能是 200 假成功。"""
    from office_agent.api.core.exceptions import TaskStateError

    with pytest.raises(TaskStateError) as excinfo:
        _run(monkeypatch, "success")

    assert excinfo.value.status_code == 409
    assert excinfo.value.error_code == "TASK_STATE_ERROR"


def test_terminal_status_leaves_status_untouched(monkeypatch):
    from office_agent.api.core.exceptions import TaskStateError
    from office_agent.api.router import task as task_router
    import office_agent.database.repository as repository_module
    import office_agent.task_queue as queue_module
    from types import SimpleNamespace

    task = SimpleNamespace(id="t-1", status="success")
    monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)

    class _TaskRepo:
        def __init__(self, _session):
            pass

        def get_by_id(self, task_id):
            return task

        def cancel_task(self, task_id):
            return False

    monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
    monkeypatch.setattr(task_router, "_get_db_session", _Session)
    original = task_router.task_manager.tasks
    task_router.task_manager.tasks = {}
    try:
        with pytest.raises(TaskStateError):
            asyncio.run(task_router._cancel_task_impl("t-1"))
    finally:
        task_router.task_manager.tasks = original

    assert task.status == "success"


# ------------------------------------------------------- 不存在：保持 404


def test_missing_task_still_raises_not_found(monkeypatch):
    from office_agent.api.core.exceptions import TaskNotFoundError

    task_router, original = _install(monkeypatch, {})
    try:
        with pytest.raises(TaskNotFoundError):
            asyncio.run(task_router._cancel_task_impl("missing"))
    finally:
        task_router.task_manager.tasks = original


def test_missing_task_not_found_status_is_404(monkeypatch):
    from office_agent.api.core.exceptions import TaskNotFoundError

    task_router, original = _install(monkeypatch, {})
    try:
        with pytest.raises(TaskNotFoundError) as excinfo:
            asyncio.run(task_router._cancel_task_impl("missing"))
    finally:
        task_router.task_manager.tasks = original

    assert excinfo.value.status_code == 404


# ------------------------------------------- repository 返回 False 必须被处理


def test_repository_false_is_not_treated_as_success(monkeypatch):
    """repo.cancel_task 返回 False 时调用方必须处理，不能静默当成功。"""
    from office_agent.api.core.exceptions import TaskStateError
    from office_agent.api.router import task as task_router
    import office_agent.database.repository as repository_module
    import office_agent.task_queue as queue_module

    monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)

    class _AlwaysFalseRepo:
        def __init__(self, _session):
            pass

        def get_by_id(self, task_id):
            return SimpleNamespace(id=task_id, status="failed")

        def cancel_task(self, task_id):
            return False

    monkeypatch.setattr(repository_module, "TaskRepository", _AlwaysFalseRepo)
    monkeypatch.setattr(task_router, "_get_db_session", _Session)
    original = task_router.task_manager.tasks
    task_router.task_manager.tasks = {}
    try:
        with pytest.raises(TaskStateError):
            asyncio.run(task_router._cancel_task_impl("t-1"))
    finally:
        task_router.task_manager.tasks = original


# ------------------------------------------------ 内存（DB 不可用）分支同样


def test_memory_terminal_task_does_not_report_success(monkeypatch):
    """数据库不可用时，内存中的终态任务也不得报"已取消"。"""
    from office_agent.api.core.exceptions import TaskStateError
    from office_agent.api.router import task as task_router
    import office_agent.task_queue as queue_module

    monkeypatch.setattr(task_router, "_get_db_session", lambda: None)
    monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)
    original = task_router.task_manager.tasks
    task_router.task_manager.create_memory_task(
        task_id="mem-done", task_type="word_format",
        instruction="x", status="success",
    )
    try:
        with pytest.raises(TaskStateError):
            asyncio.run(task_router._cancel_task_impl("mem-done"))
    finally:
        task_router.task_manager.tasks = original

    assert task_router.task_manager.tasks is original


def test_memory_active_task_still_cancels(monkeypatch):
    from office_agent.api.router import task as task_router
    import office_agent.task_queue as queue_module

    monkeypatch.setattr(task_router, "_get_db_session", lambda: None)
    monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)
    original = task_router.task_manager.tasks
    task = task_router.task_manager.create_memory_task(
        task_id="mem-run", task_type="word_format",
        instruction="x", status="running",
    )
    try:
        resp = asyncio.run(task_router._cancel_task_impl("mem-run"))
    finally:
        task_router.task_manager.tasks = original

    assert resp.message == "任务已取消"
    assert task.status == "cancelled"
