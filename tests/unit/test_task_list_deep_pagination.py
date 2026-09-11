"""P1-5 回归测试：任务列表深分页不得被 MAX_LIMIT 截断。

历史缺陷：
    数据库可用但存在内存快照时走"合并"分支，该分支固定
    ``repo.find(offset=0, limit=MAX_LIMIT)`` 先取前 1000 行，
    再在内存里 ``merged[start:start + page_size]`` 切片。

    当真实任务数超过 MAX_LIMIT 时，深页数据根本没被读出来，
    内存切片无法补救——offset=1200 的请求恒定返回空页。

修复：
    把 offset 真正下推数据库，只取"足以覆盖本页"的窗口。
    内存任务按 created_at 与 DB 行交错，会把窗口内的 DB 行往后挤，
    因此窗口要向前多取 ``len(memory_only)`` 行。

本文件全部使用 mock：不连接真实数据库。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

MAX_LIMIT = 1000
BASE_TIME = datetime(2026, 8, 31, 12, 0, 0)


def _row(i: int):
    """构造一条仿真 DB 行：下标越小越新（created_at 越大）。"""
    return SimpleNamespace(
        id=f"db-{i:04d}",
        task_type="word_format",
        agent_name="word_agent",
        status="success",
        progress=100,
        current_step="done",
        instruction=f"inst-{i}",
        error_message=None,
        input_file_ids="[]",
        output_file_ids="[]",
        result_json=None,
        created_at=(BASE_TIME - timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
        started_at=None,
        finished_at=None,
        duration_ms=0,
        quality_score=None,
        parent_task_id=None,
        revision_number=1,
    )


class _FileRepo:
    def get_by_id(self, _file_id):
        return None


class _Session:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _install(monkeypatch, rows, memory_tasks):
    """装配 fake repository / session / 内存任务，返回 task_router 模块。"""
    from office_agent.api.router import task as task_router
    import office_agent.database.repository as repository_module
    import office_agent.task_queue as queue_module

    class _TaskRepo:
        def __init__(self, _session):
            pass

        def find(self, offset=0, limit=100, order_by=None,
                 descending=False, **filters):
            # 复刻 BaseRepository._page 的硬约束：1 <= limit <= MAX_LIMIT
            assert 1 <= limit <= MAX_LIMIT, f"limit 越界: {limit}"
            assert offset >= 0, f"offset 必须非负: {offset}"
            return rows[offset:offset + limit]

        def count(self, **filters):
            return len(rows)

        def filter_existing_ids(self, task_ids):
            known = {r.id for r in rows}
            return {i for i in task_ids if i in known}

    monkeypatch.setattr(repository_module, "TaskRepository", _TaskRepo)
    monkeypatch.setattr(repository_module, "FileRepository", lambda _s: _FileRepo())
    monkeypatch.setattr(task_router, "_get_db_session", _Session)
    monkeypatch.setattr(queue_module, "cancel_task", lambda _task_id: None)
    original = task_router.task_manager.tasks
    task_router.task_manager.tasks = {t.task_id: t for t in memory_tasks}
    return task_router, original


def _memory_task(task_router, task_id, created_at, status="running"):
    task = task_router.task_manager.create_memory_task(
        task_id=task_id, task_type="word_format",
        instruction="mem", status=status,
    )
    task.created_at = created_at
    return task


def _ids(response):
    return [t.task_id for t in response.data.tasks]


# --------------------------------------------------------------- 深分页核心


def test_deep_page_beyond_max_limit_returns_real_rows(monkeypatch):
    """1500 条任务请求 offset=1200：必须返回 db-1200..db-1249，而不是空页。"""
    rows = [_row(i) for i in range(1500)]
    task_router, original = _install(monkeypatch, rows, [])
    # 需要一个内存快照才会走合并分支；该任务同时存在于 DB -> memory_only 为空
    mem = _memory_task(task_router, "db-0000", "2026-09-01 00:00:00")
    task_router.task_manager.tasks = {mem.task_id: mem}
    try:
        resp = asyncio.run(task_router._list_tasks_impl(page=25, page_size=50))
    finally:
        task_router.task_manager.tasks = original

    assert _ids(resp) == [f"db-{i:04d}" for i in range(1200, 1250)]
    assert resp.data.total == 1500


def test_offset_just_above_max_limit(monkeypatch):
    """offset=1000 正好越过旧窗口边界，不得返回空。"""
    rows = [_row(i) for i in range(1500)]
    task_router, original = _install(monkeypatch, rows, [])
    mem = _memory_task(task_router, "db-0000", "2026-09-01 00:00:00")
    task_router.task_manager.tasks = {mem.task_id: mem}
    try:
        # page_size=10, page=101 -> start=1000
        resp = asyncio.run(task_router._list_tasks_impl(page=101, page_size=10))
    finally:
        task_router.task_manager.tasks = original

    assert _ids(resp) == [f"db-{i:04d}" for i in range(1000, 1010)]


def test_page_straddling_max_limit_boundary(monkeypatch):
    """start=995/page_size=10 跨越旧窗口尾部：旧实现只能给出 5 条。"""
    rows = [_row(i) for i in range(1500)]
    task_router, original = _install(monkeypatch, rows, [])
    mem = _memory_task(task_router, "db-0000", "2026-09-01 00:00:00")
    task_router.task_manager.tasks = {mem.task_id: mem}
    try:
        # page_size=10, page=100 -> start=990；用 page_size=5, page=200 -> start=995
        resp = asyncio.run(task_router._list_tasks_impl(page=200, page_size=5))
        assert _ids(resp) == [f"db-{i:04d}" for i in range(995, 1000)]
        resp = asyncio.run(task_router._list_tasks_impl(page=100, page_size=10))
        assert _ids(resp) == [f"db-{i:04d}" for i in range(990, 1000)]
    finally:
        task_router.task_manager.tasks = original


def test_last_page_of_large_dataset(monkeypatch):
    """最后一页（可能不满）必须返回真实剩余行。"""
    rows = [_row(i) for i in range(1500)]
    task_router, original = _install(monkeypatch, rows, [])
    mem = _memory_task(task_router, "db-0000", "2026-09-01 00:00:00")
    task_router.task_manager.tasks = {mem.task_id: mem}
    try:
        # page_size=100, page=15 -> start=1400，剩 100 条
        resp = asyncio.run(task_router._list_tasks_impl(page=15, page_size=100))
        assert _ids(resp) == [f"db-{i:04d}" for i in range(1400, 1500)]
        # page=16 -> start=1500，空页
        resp = asyncio.run(task_router._list_tasks_impl(page=16, page_size=100))
        assert _ids(resp) == []
        assert resp.data.total == 1500
    finally:
        task_router.task_manager.tasks = original


# ------------------------------------------------------ 内存 + DB 混合语义


def test_memory_only_tasks_sort_ahead_of_db_rows(monkeypatch):
    """memory-only 快照按 created_at 排在 DB 行之前。"""
    rows = [_row(i) for i in range(10)]
    task_router, original = _install(monkeypatch, rows, [])
    newest = _memory_task(task_router, "mem-new", "2026-09-02 00:00:00")
    older = _memory_task(task_router, "mem-old", "2026-09-01 00:00:00")
    task_router.task_manager.tasks = {t.task_id: t for t in (newest, older)}
    try:
        resp = asyncio.run(task_router._list_tasks_impl(page=1, page_size=5))
    finally:
        task_router.task_manager.tasks = original

    assert _ids(resp) == ["mem-new", "mem-old", "db-0000", "db-0001", "db-0002"]
    assert resp.data.total == 12  # 10 DB + 2 memory-only


def test_memory_task_already_in_db_is_not_duplicated(monkeypatch):
    """内存快照对应的任务已在 DB（即便在深页）时，不得重复计数。"""
    rows = [_row(i) for i in range(1500)]
    task_router, original = _install(monkeypatch, rows, [])
    # db-1200 位于旧窗口（前 1000 行）之外；沿用原行 created_at 以保持排序位置
    mem = _memory_task(task_router, "db-1200", rows[1200].created_at)
    task_router.task_manager.tasks = {mem.task_id: mem}
    try:
        resp = asyncio.run(task_router._list_tasks_impl(page=25, page_size=50))
    finally:
        task_router.task_manager.tasks = original

    ids = _ids(resp)
    assert len(ids) == len(set(ids)), "出现重复任务"
    assert ids[0] == "db-1200"
    assert resp.data.total == 1500, "已落库的内存快照不得被重复计入 total"


def test_memory_only_tasks_shift_deep_pages_correctly(monkeypatch):
    """memory-only 任务占位后，深页取到的仍是正确行。"""
    rows = [_row(i) for i in range(1500)]
    task_router, original = _install(monkeypatch, rows, [])
    a = _memory_task(task_router, "mem-a", "2026-09-02 00:00:00")
    b = _memory_task(task_router, "mem-b", "2026-09-01 00:00:00")
    task_router.task_manager.tasks = {t.task_id: t for t in (a, b)}
    try:
        # 完整列表：0=mem-a, 1=mem-b, 2=db-0000, ... start=1200 -> db-1198
        resp = asyncio.run(task_router._list_tasks_impl(page=25, page_size=50))
    finally:
        task_router.task_manager.tasks = original

    assert _ids(resp) == [f"db-{i:04d}" for i in range(1198, 1248)]
    assert resp.data.total == 1502


# ------------------------------------------------------------ 不回归第一页


def test_first_page_unchanged(monkeypatch):
    """第一页行为必须保持原样。"""
    rows = [_row(i) for i in range(1500)]
    task_router, original = _install(monkeypatch, rows, [])
    mem = _memory_task(task_router, "db-0000", "2026-09-01 00:00:00")
    task_router.task_manager.tasks = {mem.task_id: mem}
    try:
        resp = asyncio.run(task_router._list_tasks_impl(page=1, page_size=20))
    finally:
        task_router.task_manager.tasks = original

    assert _ids(resp) == [f"db-{i:04d}" for i in range(20)]
    assert resp.data.total == 1500
    assert resp.data.page == 1
    assert resp.data.page_size == 20


@pytest.mark.parametrize("page,page_size", [(1, 1), (2, 3), (7, 13), (501, 3)])
def test_limit_boundaries_never_exceed_max_limit(monkeypatch, page, page_size):
    """任何分页参数下传给 repo 的 limit 都不得超过 MAX_LIMIT（fake 内断言）。"""
    rows = [_row(i) for i in range(1500)]
    task_router, original = _install(monkeypatch, rows, [])
    mem = _memory_task(task_router, "db-0000", "2026-09-01 00:00:00")
    task_router.task_manager.tasks = {mem.task_id: mem}
    try:
        resp = asyncio.run(
            task_router._list_tasks_impl(page=page, page_size=page_size))
    finally:
        task_router.task_manager.tasks = original

    assert len(resp.data.tasks) <= page_size
