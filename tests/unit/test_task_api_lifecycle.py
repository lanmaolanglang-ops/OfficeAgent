"""Task state-machine and API contract regression tests."""

from __future__ import annotations

import asyncio
import importlib
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest


def test_task_state_machine_filters_paginates_and_serializes(monkeypatch):
    from office_agent.api.core.task_manager import Task, TaskManager, TaskStatus

    manager_module = importlib.import_module("office_agent.api.core.task_manager")
    monkeypatch.setattr(manager_module.time, "time", lambda: 100.0)
    first = Task("word_format", "format", agent="word_agent", file_ids=["file_a"])
    first.start()
    first.update(progress=140, step="render")
    first.complete({"ok": True}, ["file_out"], quality_score=92.5)
    assert first.status == TaskStatus.SUCCESS.value
    assert first.progress == 100
    assert first.duration_ms == 0
    assert first.steps[-1]["status"] == "completed"
    assert first.to_dict()["output_files"] == ["file_out"]

    second = Task("ppt_generate", "slides", agent="ppt_agent")
    second.start()
    second.update(progress=-5, step="plan", status="waiting")
    assert second.status == TaskStatus.QUEUED.value
    assert TaskStatus.WAITING is TaskStatus.QUEUED
    second.fail("model unavailable")
    assert second.progress == 0
    assert second.steps[-1]["status"] == "failed"

    pending = Task("word_format", "pending", agent="word_agent")
    queued = Task("ppt_generate", "queued", agent="ppt_agent")
    queued.update(status=TaskStatus.QUEUED)
    manager = TaskManager()
    manager.tasks = {t.task_id: t for t in (first, second, pending, queued)}
    assert manager.get_task(first.task_id) is first
    assert manager.get_active_count() == 2
    filtered, total = manager.list_tasks(status="success", agent="word_agent")
    assert filtered == [first] and total == 1
    page, total = manager.list_tasks(page=2, page_size=2)
    assert len(page) == 2 and total == 4
    legacy_filtered, total = manager.list_tasks(status="waiting")
    assert legacy_filtered == [queued] and total == 1


def test_task_router_helpers_validate_files_and_remove_local_paths(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from office_agent.api.router import task as task_router
    import office_agent.storage.storage_service as storage_module

    good_path = tmp_path / "source.docx"
    good_path.write_bytes(b"content")

    class _Files:
        def __init__(self, item):
            self.item = item

        def get_by_id(self, _file_id):
            return self.item

    storage = SimpleNamespace(get_file_path=lambda _file_id: str(good_path))
    monkeypatch.setattr(storage_module, "get_storage_service", lambda: storage)
    db_file = SimpleNamespace(
        id="file_a", original_name="report.docx", filename="stored.docx", status="ready",
    )
    assert task_router._resolve_input_files(["file_a"], _Files(db_file)) == [str(good_path)]
    assert task_router._file_id_list_to_infos(["file_a"], _Files(db_file))[0]["download_url"].endswith("file_a")
    assert task_router._sanitize_result({"output_path": "C:/secret", "value": 3}) == {"value": 3}
    assert task_router._sanitize_result("plain") == "plain"

    with pytest.raises(HTTPException) as deleted:
        task_router._resolve_input_files(["file_a"], _Files(SimpleNamespace(status="deleted")))
    assert deleted.value.status_code == 404
    storage.get_file_path = lambda _file_id: str(tmp_path / "missing.docx")
    with pytest.raises(HTTPException) as missing:
        task_router._resolve_input_files(["file_a"], _Files(db_file))
    assert missing.value.status_code == 422
    storage.get_file_path = lambda _file_id: (_ for _ in ()).throw(FileNotFoundError())
    with pytest.raises(HTTPException) as absent:
        task_router._resolve_input_files(["file_a"], _Files(db_file))
    assert absent.value.status_code == 404


def test_create_task_queues_work_and_persists_queue_failures(monkeypatch):
    from office_agent.api.router import task as task_router
    from office_agent.api.schemas.request import TaskCreateRequest
    import office_agent.database.repository as repository_module
    import office_agent.database.session as session_module
    import office_agent.task_queue as queue_module

    records = {}

    class _TaskRepo:
        def __init__(self, _session):
            pass

        def create_task(self, **kwargs):
            task_id = f"db_{len(records) + 1}"
            records[task_id] = {"created": kwargs}
            return SimpleNamespace(id=task_id)

        def fail_task(self, task_id, error):
            records[task_id]["error"] = error

    class _FileRepo:
        def __init__(self, _session):
            pass

    @contextmanager
    def _scope():
        yield object()

    submitted = []
    monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
    monkeypatch.setattr(repository_module, "FileRepository", _FileRepo)
    monkeypatch.setattr(session_module, "session_scope", _scope)
    monkeypatch.setattr(queue_module, "init_worker", lambda: None)
    monkeypatch.setattr(queue_module, "queue_name_for_task_type", lambda value: f"queue.{value}")
    monkeypatch.setattr(queue_module, "submit_task", lambda **kwargs: submitted.append(kwargs))

    request = TaskCreateRequest(
        task_type="word_format", instruction="format it", priority="unexpected",
        options={"theme": "formal"},
    )
    response = asyncio.run(task_router._create_task_impl(request))
    assert response.data.status == "queued"
    assert submitted[0]["priority"] == "normal"
    assert submitted[0]["task_name"] == "queue.word_format"

    monkeypatch.setattr(queue_module, "submit_task", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("broker secret")))
    failed = asyncio.run(task_router._create_task_impl(request))
    assert failed.data.status == "failed"
    assert "\u4efb\u52a1\u961f\u5217\u4e0d\u53ef\u7528" in records[failed.data.task_id]["error"]


def test_task_routes_use_memory_fallback(monkeypatch):
    from office_agent.api.core.exceptions import TaskNotFoundError
    from office_agent.api.core.task_manager import Task
    from office_agent.api.router import task as task_router
    from office_agent.api.schemas.request import FeedbackRequest
    import office_agent.task_queue as queue_module

    original = task_router.task_manager.tasks
    item = Task("excel_analysis", "analyze", agent="excel_agent")
    item.start()
    task_router.task_manager.tasks = {item.task_id: item}
    monkeypatch.setattr(task_router, "_get_db_session", lambda: None)
    monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)
    try:
        found = asyncio.run(task_router.get_task(item.task_id))
        assert found.data.status == "running"
        listing = asyncio.run(task_router._list_tasks_impl(agent="excel_agent", page=1, page_size=5))
        assert listing.data.total == 1
        cancelled = asyncio.run(task_router._cancel_task_impl(item.task_id))
        assert cancelled.message == "\u4efb\u52a1\u5df2\u53d6\u6d88"
        feedback = asyncio.run(task_router.task_feedback(item.task_id, FeedbackRequest(rating=5, comment="good")))
        assert feedback.data["rating"] == 5
        with pytest.raises(TaskNotFoundError):
            asyncio.run(task_router.get_task("missing"))
        with pytest.raises(TaskNotFoundError):
            asyncio.run(task_router._cancel_task_impl("missing"))
    finally:
        task_router.task_manager.tasks = original


def test_task_routes_read_update_database_and_close_sessions(monkeypatch):
    from office_agent.api.router import task as task_router
    from office_agent.api.schemas.request import FeedbackRequest
    import office_agent.database.repository as repository_module
    import office_agent.task_queue as queue_module

    now = datetime(2026, 8, 31, 12, 0)
    db_task = SimpleNamespace(
        id="db_task", task_type="word_format", agent_name="word_agent",
        status="success", progress=100, current_step="done", instruction="format",
        error_message=None, input_file_ids='["file_in"]', output_file_ids='["file_out"]',
        result_json='{"output_path":"C:/private.docx","answer":42}', created_at=now,
        started_at=now, finished_at=now, duration_ms=250, quality_score=96.0,
        parent_task_id=None, revision_number=1,
    )
    files = {
        "file_in": SimpleNamespace(id="file_in", original_name="in.docx", filename="in.bin", status="ready"),
        "file_out": SimpleNamespace(id="file_out", original_name="out.docx", filename="out.bin", status="ready"),
    }
    sessions = []

    class _Session:
        def __init__(self):
            self.closed = False
            self.commits = 0
            sessions.append(self)

        def close(self):
            self.closed = True

        def commit(self):
            self.commits += 1

    class _TaskRepo:
        def __init__(self, _session):
            pass

        def get_by_id(self, task_id):
            return db_task if task_id == "db_task" else None

        def find(self, **kwargs):
            assert kwargs["status"] == "success"
            return [db_task]

        def count(self, **kwargs):
            return 1

        def cancel_task(self, task_id):
            assert task_id == "db_task"

        def add_feedback(self, task_id, rating, comment):
            assert (task_id, rating, comment) == ("db_task", 4, "useful")

    class _FileRepo:
        def __init__(self, _session):
            pass

        def get_by_id(self, file_id):
            return files.get(file_id)

    monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
    monkeypatch.setattr(repository_module, "FileRepository", _FileRepo)
    monkeypatch.setattr(task_router, "_get_db_session", _Session)
    monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)

    found = asyncio.run(task_router.get_task("db_task"))
    assert found.data.result == {"answer": 42}
    assert found.data.output_files[0].filename == "out.docx"
    listing = asyncio.run(task_router._list_tasks_impl(status="success", page=1, page_size=10))
    assert listing.data.total == 1
    assert asyncio.run(task_router._cancel_task_impl("db_task")).message == "\u4efb\u52a1\u5df2\u53d6\u6d88"
    assert asyncio.run(task_router.task_feedback("db_task", FeedbackRequest(rating=4, comment="useful"))).message == "\u53cd\u9988\u5df2\u63d0\u4ea4"
    assert all(session.closed for session in sessions)
    assert sessions[-1].commits == 1


def test_task_json_corruption_isolated():
    from office_agent.api.router.task import _safe_json_object

    assert _safe_json_object('{"ok": 1}', {}) == {"ok": 1}
    assert _safe_json_object("{broken", {}) == {}
    assert _safe_json_object(None, []) == []


def test_unknown_task_type_rejected_before_database_write():
    from fastapi import HTTPException
    from office_agent.api.router.task import _create_task_impl
    from office_agent.api.schemas.request import TaskCreateRequest

    request = TaskCreateRequest(task_type="typo_task", instruction="do it")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_create_task_impl(request))
    assert exc.value.status_code == 422


def test_agent_name_derivation_has_single_none_sentinel():
    """清单 303：Agent 推导只有 None 一种"无值"口径，响应层显式 or ""。"""
    import inspect
    from office_agent.api.router import task as task_module

    derive = task_module._agent_name_for_task_type
    assert derive("word_format") == "word_agent"
    assert derive("ppt_generate") == "ppt_agent"
    assert derive("excel_analyze") == "excel_agent"
    assert derive("general") is None
    assert derive(None) is None
    assert derive("") is None

    # 源码级守卫：create_task 内不再出现内联三元推导的两份拷贝
    src = inspect.getsource(task_module._create_task_impl)
    assert src.count('_agent_name_for_task_type(req.task_type)') == 1
    assert 'split("_")[0]' not in src
