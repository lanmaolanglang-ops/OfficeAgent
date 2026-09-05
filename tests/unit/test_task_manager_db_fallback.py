"""TaskManager database-failure fallback contract.

These tests pin the scoped root-problem contract:
- DB is authoritative when available;
- degraded memory snapshots are the process-local authority for a task after a
  database write failure, and are never mistaken for persisted records;
- create/read/list/update/cancel stay consistent under database failure;
- SQLAlchemy/driver errors trigger fallback, while programming errors propagate.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    @property
    def messages(self):
        return [r.getMessage() for r in self.records]


@pytest.fixture
def capture_logs():
    attached = []

    def _cap(name):
        target = logging.getLogger(name)
        handler = _ListHandler()
        prev_level = target.level
        target.setLevel(logging.DEBUG)
        target.addHandler(handler)
        attached.append((target, handler, prev_level))
        return handler

    yield _cap

    for target, handler, prev_level in attached:
        target.removeHandler(handler)
        target.setLevel(prev_level)


@pytest.fixture(autouse=True)
def reset_task_manager():
    from office_agent.api.core.task_manager import task_manager

    saved = task_manager.tasks
    task_manager.tasks = {}
    try:
        yield task_manager
    finally:
        task_manager.tasks = saved


class _Session:
    def __init__(self):
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def close(self):
        self.closed = True

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _fake_session_scope():
    @contextmanager
    def scope():
        yield _Session()

    return scope


# ---------------------------------------------------------------- TaskManager

class TestTaskManagerDegradedSnapshot:
    def test_memory_snapshot_is_explicitly_degraded(self):
        from office_agent.api.core.task_manager import TaskManager

        manager = TaskManager()
        task = manager.create_memory_task(
            task_type="word_format",
            instruction="format",
            agent="word_agent",
            user_id="user-1",
            status="pending",
        )

        assert task.storage == "memory"
        assert task.degraded is True
        assert manager.get_task(task.task_id) is task

    def test_memory_cancel_marks_terminal(self):
        from office_agent.api.core.task_manager import TaskManager, TaskStatus

        manager = TaskManager()
        task = manager.create_memory_task(task_type="ppt_generate", status="queued")
        assert manager.cancel_memory_task(task.task_id) is True
        assert task.status == TaskStatus.CANCELLED.value


# ---------------------------------------------------------------- create route

class TestCreateDbFailure:
    def test_commit_failure_creates_degraded_task_and_queues(self, monkeypatch):
        from office_agent.api.router import task as task_router
        from office_agent.api.schemas.request import TaskCreateRequest
        import office_agent.database.repository as repository_module
        import office_agent.database.session as session_module
        import office_agent.task_queue as queue_module

        class _TaskRepo:
            def __init__(self, _session):
                pass

            def create_task(self, **kwargs):
                raise SQLAlchemyError("insert failed")

        monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
        monkeypatch.setattr(session_module, "session_scope", _fake_session_scope())
        monkeypatch.setattr(queue_module, "init_worker", lambda: None)
        monkeypatch.setattr(queue_module, "queue_name_for_task_type",
                            lambda value: f"queue.{value}")
        submitted = []
        monkeypatch.setattr(queue_module, "submit_task",
                            lambda **kwargs: submitted.append(kwargs))

        request = TaskCreateRequest(task_type="word_format", instruction="format it")
        response = asyncio.run(task_router.create_task(request))

        assert response.data.degraded is True
        assert response.data.storage == "memory"
        assert response.data.status == "queued"
        assert submitted[0]["task_id"] == response.data.task_id
        assert task_router.task_manager.get_task(response.data.task_id).status == "queued"

    def test_input_read_failure_is_fail_closed(self, monkeypatch):
        from office_agent.api.core.exceptions import APIError
        from office_agent.api.router import task as task_router
        from office_agent.api.schemas.request import TaskCreateRequest
        import office_agent.database.repository as repository_module
        import office_agent.database.session as session_module

        class _FileRepo:
            def __init__(self, _session):
                pass

            def get_by_id(self, file_id):
                raise SQLAlchemyError("read failed")

        monkeypatch.setattr(repository_module, "FileRepository", _FileRepo)
        monkeypatch.setattr(session_module, "session_scope", _fake_session_scope())

        request = TaskCreateRequest(
            task_type="word_format",
            instruction="format it",
            file_ids=["file_missing"],
        )
        with pytest.raises(APIError) as exc:
            asyncio.run(task_router.create_task(request))
        assert exc.value.status_code == 503
        assert exc.value.error_code == "DATABASE_UNAVAILABLE"

    def test_programming_error_during_create_is_not_degraded(self, monkeypatch):
        from office_agent.api.router import task as task_router
        from office_agent.api.schemas.request import TaskCreateRequest
        import office_agent.database.repository as repository_module
        import office_agent.database.session as session_module

        class _TaskRepo:
            def __init__(self, _session):
                pass

            def create_task(self, **kwargs):
                raise TypeError("bad argument")

        monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
        monkeypatch.setattr(session_module, "session_scope", _fake_session_scope())

        with pytest.raises(TypeError):
            asyncio.run(task_router.create_task(
                TaskCreateRequest(task_type="word_format", instruction="x"),
            ))


# ---------------------------------------------------------------- read routes

class TestReadDbFailure:
    def test_get_falls_back_to_degraded_snapshot(self, monkeypatch):
        from office_agent.api.router import task as task_router
        import office_agent.database.repository as repository_module

        class _Session:
            def close(self):
                pass

        class _TaskRepo:
            def __init__(self, _session):
                pass

            def get_by_id(self, task_id):
                raise SQLAlchemyError("read failed")

        class _FileRepo:
            def __init__(self, _session):
                pass

        monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
        monkeypatch.setattr(repository_module, "FileRepository", _FileRepo)
        monkeypatch.setattr(task_router, "_get_db_session", lambda: _Session())

        task = task_router.task_manager.create_memory_task(
            task_id="task_degraded", task_type="excel_analyze",
            instruction="analyze", status="running",
        )

        response = asyncio.run(task_router.get_task(task.task_id))
        assert response.data.status == "running"
        assert response.data.degraded is True

    def test_list_falls_back_and_memory_task_is_visible(self, monkeypatch):
        from office_agent.api.router import task as task_router
        import office_agent.database.repository as repository_module

        class _Session:
            def close(self):
                pass

        class _TaskRepo:
            def __init__(self, _session):
                pass

            def find(self, **kwargs):
                raise SQLAlchemyError("read failed")

        class _FileRepo:
            def __init__(self, _session):
                pass

        monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
        monkeypatch.setattr(repository_module, "FileRepository", _FileRepo)
        monkeypatch.setattr(task_router, "_get_db_session", lambda: _Session())

        task = task_router.task_manager.create_memory_task(
            task_id="task_degraded", task_type="word_format",
            instruction="format", agent="word_agent", status="queued",
        )

        response = asyncio.run(
            task_router.list_tasks(agent="word_agent", page=1, page_size=5),
        )
        assert response.data.total == 1
        assert response.data.tasks[0].task_id == task.task_id
        assert response.data.tasks[0].degraded is True

    def test_get_and_list_agree_when_database_recovers(self, monkeypatch):
        from office_agent.api.router import task as task_router
        import office_agent.database.repository as repository_module

        db_task = SimpleNamespace(
            id="db_task", task_type="word_format", agent_name="word_agent",
            status="success", progress=100, current_step="done",
            instruction="format", error_message=None, input_file_ids=None,
            output_file_ids=None, result_json=None, created_at="2026-09-01 10:00:00",
            started_at=None, finished_at=None, duration_ms=None,
            quality_score=None, parent_task_id=None, revision_number=1,
        )

        class _Session:
            def close(self):
                pass

        class _TaskRepo:
            def __init__(self, _session):
                pass

            def get_by_id(self, task_id):
                return db_task if task_id == "db_task" else None

            def find(self, **kwargs):
                return [db_task]

            def count(self, **kwargs):
                return 1

        class _FileRepo:
            def __init__(self, _session):
                pass

            def get_by_id(self, file_id):
                return None

        monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
        monkeypatch.setattr(repository_module, "FileRepository", _FileRepo)
        monkeypatch.setattr(task_router, "_get_db_session", lambda: _Session())

        got = asyncio.run(task_router.get_task("db_task"))
        listed = asyncio.run(task_router.list_tasks(page=1, page_size=5))
        assert got.data.storage == "persisted"
        assert got.data.degraded is False
        assert listed.data.total == 1
        assert listed.data.tasks[0].task_id == "db_task"


# ---------------------------------------------------------------- cancel route

class TestCancelDbFailure:
    def test_cancel_uses_memory_when_db_unavailable(self, monkeypatch):
        from office_agent.api.router import task as task_router
        import office_agent.task_queue as queue_module

        monkeypatch.setattr(task_router, "_get_db_session", lambda: None)
        monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)
        task = task_router.task_manager.create_memory_task(
            task_id="task_degraded", task_type="ppt_generate",
            instruction="deck", status="running",
        )

        response = asyncio.run(task_router.cancel_task(task.task_id))
        assert response.message == "任务已取消"
        assert task_router.task_manager.get_task(task.task_id).status == "cancelled"


# ---------------------------------------------------------------- worker writes

class _FakeWorkerRepo:
    def __init__(self, current=None, complete_error=None, get_error=None):
        self.current = current
        self.complete_error = complete_error
        self.get_error = get_error

    def get_by_id(self, task_id):
        if self.get_error:
            raise self.get_error
        return self.current

    def start_task(self, task_id):
        return True

    def update(self, task_id, data):
        return None

    def complete_task(self, task_id, **kwargs):
        if self.complete_error:
            raise self.complete_error
        return True

    def fail_task(self, task_id, error, duration_ms=None):
        return True

    def cancel_task(self, task_id):
        return True


class TestWorkerDbFallback:
    def _worker(self, repo):
        from office_agent.task_queue.worker import LocalWorker

        worker = LocalWorker.__new__(LocalWorker)
        worker._session_factory = lambda: _Session()
        return worker, repo

    def test_status_write_failure_creates_degraded_snapshot(self, monkeypatch):
        import office_agent.database.repository as repository_module
        from office_agent.api.core.task_manager import task_manager

        current = SimpleNamespace(
            task_type="word_format", instruction="format", agent_name="word_agent",
            user_id="user-1", input_file_ids=None, options_json=None,
            status="pending",
        )
        repo = _FakeWorkerRepo(
            current=current, complete_error=SQLAlchemyError("commit failed"),
        )
        monkeypatch.setattr(repository_module, "TaskRepository", lambda _session: repo)
        worker, _ = self._worker(repo)

        worker._update_status("task_db", "success", duration_ms=10)
        task = task_manager.get_task("task_db")
        assert task is not None
        assert task.status == "success"
        assert task.degraded is True
        assert task.instruction == "format"

    def test_status_programming_error_propagates(self, monkeypatch):
        import office_agent.database.repository as repository_module

        repo = _FakeWorkerRepo(get_error=TypeError("bad access"))
        monkeypatch.setattr(repository_module, "TaskRepository", lambda _session: repo)
        worker, _ = self._worker(repo)

        with pytest.raises(TypeError):
            worker._update_status("task_db", "running", progress=0)

    def test_completion_persistence_failure_preserves_result(self, monkeypatch):
        import office_agent.database.repository as repository_module
        from office_agent.api.core.task_manager import task_manager

        current = SimpleNamespace(
            task_type="word_format", instruction="format", agent_name="word_agent",
            user_id="user-1", input_file_ids=None, options_json=None,
            status="pending",
        )
        repo = _FakeWorkerRepo(
            current=current, complete_error=SQLAlchemyError("commit failed"),
        )
        monkeypatch.setattr(repository_module, "TaskRepository", lambda _session: repo)
        worker, _ = self._worker(repo)

        result = {"status": "success", "output_files": [], "answer": 42}
        worker._complete("task_db", result, duration_ms=7)
        task = task_manager.get_task("task_db")
        assert task is not None
        assert task.status == "success"
        assert task.result == result
        assert task.duration_ms == 7


# ---------------------------------------------------------------- logging

class TestFallbackLogging:
    def test_fallback_log_has_contract_fields_and_no_secrets(self, monkeypatch):
        import importlib

        task_manager_module = importlib.import_module(
            "office_agent.api.core.task_manager",
        )

        class _RecordingLogger:
            def __init__(self):
                self.calls = []

            def warning(self, message, *args, **kwargs):
                self.calls.append((message, args, kwargs))

        logger = _RecordingLogger()
        monkeypatch.setattr(task_manager_module, "logger", logger)
        task_manager_module.log_db_fallback(
            "create_task", "task_abc",
            SQLAlchemyError("secret password=hunter2"),
        )

        message, _args, kwargs = logger.calls[-1]
        extra = kwargs["extra"]
        assert extra["operation"] == "create_task"
        assert extra["task_id"] == "task_abc"
        assert extra["degraded_mode"] is True
        assert extra["normalized_failure_category"] == "sqlalchemy_error"
        assert "password=hunter2" not in message
