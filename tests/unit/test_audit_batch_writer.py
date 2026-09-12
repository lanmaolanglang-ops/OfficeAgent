"""P3-2：审计持久化批量写 + 内存镜像并发安全回归。

修复前：
- 每条审计事件在请求路径上同步 新会话 + 逐事件 FK 内省 + commit；
- ``_entries`` 的 append/截断/遍历无锁，flush 与 append、clear 与
  get_entries 之间存在竞态。

安全审计事件不允许静默丢失：``log()`` 只入队（队列满时调用线程同步
兜底持久化），后台写线程小窗口聚合批量落库（一个会话一次 commit），
批失败降级为逐条隔离；``flush`` 等待排空、``close`` 排空并结束写线程。
"""
import threading
import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from office_agent.database.base import Base
from office_agent.database import models  # noqa: F401  注册全部表
from office_agent.database.models import AuditLogModel
from office_agent.security.audit import AuditLogger


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _db_rows(factory):
    with factory() as session:
        return list(session.scalars(select(AuditLogModel)))


class _CountingSessionFactory:
    """统计 commit 次数的会话工厂代理（with 协议显式实现）。"""

    def __init__(self, real, commits):
        self._real = real
        self._commits = commits

    def __call__(self):
        session = self._real()
        real_commit = session.commit

        def commit():
            self._commits.append(1)
            real_commit()

        session.commit = commit
        return session


class TestBatchedPersistence:
    def test_events_persisted_after_flush(self, factory):
        audit = AuditLogger(session_factory=factory)
        for index in range(10):
            audit.log("config_change", user_id=f"user-{index}",
                      resource="settings")
        assert audit.flush(5.0) is True
        rows = _db_rows(factory)
        assert len(rows) == 10, "flush 后全部事件必须已落库"
        assert len({row.id for row in rows}) == 10, "不重复、不丢失"

    def test_commit_count_reduced_by_batching(self, factory):
        """一个 batch window 内的 N 条事件聚合为远少于 N 的 commit。"""
        commits: list = []
        counting = _CountingSessionFactory(factory, commits)
        audit = AuditLogger(session_factory=counting,
                            batch_size=100, batch_window=0.3)
        for index in range(20):
            audit.log("model_call", status="success", user_id=f"u{index}")
        assert audit.flush(5.0) is True
        audit.close(5.0)
        assert len(commits) < 20, \
            f"20 条事件不应产生 20 次 commit（实际 {len(commits)}）"
        assert len(_db_rows(factory)) == 20

    def test_batch_failure_falls_back_per_entry(self, factory, monkeypatch):
        """批量失败降级为逐条隔离：单条坏数据不拖垮同批其它事件。"""
        audit = AuditLogger(session_factory=factory, batch_size=50,
                            batch_window=0.2)
        real_persist = audit._persist_entries

        def wrapped(entries):
            if len(entries) > 1:
                raise RuntimeError("force per-entry isolation")
            if entries[0].user_id == "poison":
                raise RuntimeError("bad entry")
            real_persist(entries)

        monkeypatch.setattr(audit, "_persist_entries", wrapped)
        for user_id in ("u0", "u1", "poison", "u3", "u4"):
            audit.log("agent_call", user_id=user_id)
        assert audit.flush(5.0) is True
        rows = _db_rows(factory)
        # 测试库没有对应用户：user_id 落 None，原身份进 details.subject_user_id
        subjects = sorted(row.details["subject_user_id"] for row in rows)
        assert subjects == ["u0", "u1", "u3", "u4"], \
            "坏条目只影响自己，其余必须落库"

    def test_queue_full_falls_back_to_sync(self, factory):
        """队列满时调用线程同步持久化——事件不丢。"""
        from office_agent.security.audit import AuditEntry

        audit = AuditLogger(session_factory=factory, queue_maxsize=1,
                            batch_window=0.0)
        # 手动填满队列，模拟数据库落后、写线程来不及消费
        audit._queue.put(AuditEntry(action="model_call", status="success"))
        audit.log("login", user_id="u2")        # 队列满 → 同步兜底
        audit.log("login", user_id="u3")        # 同上
        audit.close(10.0)
        rows = _db_rows(factory)
        subjects = {row.details.get("subject_user_id") for row in rows}
        assert {"u2", "u3"} <= subjects, "队列满时的同步兜底不得丢事件"


class TestMemoryMirrorConcurrency:
    def _mirror_logger(self):
        """只测内存镜像：任何 DB 访问立即失败（镜像操作不得依赖数据库）。"""

        def _no_db():
            raise RuntimeError("mirror tests must not touch the database")

        audit = AuditLogger(enable=True, session_factory=_no_db,
                            max_memory_entries=50, batch_window=0.0)
        audit._closed = True  # log() 走同步兜底路径，工厂失败被吞掉
        return audit

    def test_mirror_bounded(self):
        audit = self._mirror_logger()
        for index in range(200):
            audit.log("tool_call", user_id=f"u{index}")
        assert len(audit._entries) == 50, "内存镜像必须保持有界"

    def test_concurrent_append_and_read_no_loss(self):
        audit = AuditLogger(enable=True, session_factory=_no_db_factory(),
                            max_memory_entries=100000, batch_window=0.0)
        audit._closed = True
        threads_count = 4
        per_thread = 250
        barrier = threading.Barrier(threads_count + 1)

        def writer(offset):
            barrier.wait()
            for index in range(per_thread):
                audit.log("agent_call", user_id=f"u{offset}-{index}")

        def reader():
            barrier.wait()
            for _ in range(100):
                audit.get_entries(limit=1000)
                audit.get_recent_dangerous(limit=1000)

        threads = [threading.Thread(target=writer, args=(offset,))
                   for offset in range(threads_count)]
        threads.append(threading.Thread(target=reader))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(audit._entries) == threads_count * per_thread, \
            "并发 append 不得丢事件"

    def test_flush_and_append_concurrently(self, factory):
        audit = AuditLogger(session_factory=factory, batch_window=0.0,
                            queue_maxsize=100000)
        stop = threading.Event()

        def appender():
            index = 0
            while not stop.is_set():
                audit.log("model_call", status="success", user_id=f"u{index}")
                index += 1
                time.sleep(0.01)

        writer = threading.Thread(target=appender, daemon=True)
        writer.start()
        try:
            for _ in range(20):
                assert audit.flush(2.0) is True, \
                    "flush 与 append 并发不得永久阻塞"
        finally:
            stop.set()
            writer.join(timeout=5)
        audit.close(5.0)
        assert audit.flush(5.0) is True
        assert len(_db_rows(factory)) > 0


def _no_db_factory():
    def _no_db():
        raise RuntimeError("mirror tests must not touch the database")
    return _no_db


class TestCloseAndDrain:
    def test_close_drains_pending_events(self, factory):
        audit = AuditLogger(session_factory=factory, batch_size=10,
                            batch_window=0.05)
        for index in range(25):
            audit.log("config_change", user_id=f"u{index}")
        audit.close(10.0)
        assert len(_db_rows(factory)) == 25, "close 必须排空并落库全部事件"

    def test_close_is_idempotent(self, factory):
        audit = AuditLogger(session_factory=factory)
        audit.log("login", user_id="u1")
        audit.close(5.0)
        audit.close(5.0)
        assert audit._closed is True

    def test_log_after_close_persists_synchronously(self, factory):
        audit = AuditLogger(session_factory=factory)
        audit.close(5.0)
        audit.log("login", user_id="late")
        rows = _db_rows(factory)
        assert len(rows) == 1
        assert rows[0].details["subject_user_id"] == "late"


class TestLegacyForeignKeyCache:
    def test_schema_introspection_cached(self, factory, monkeypatch):
        """FK 内省按实例缓存（旧实现逐事件内省，纯开销）。"""
        import sqlalchemy

        audit = AuditLogger(session_factory=factory)
        inspect_calls = []
        original_inspect = sqlalchemy.inspect

        def counting_inspect(target):
            inspect_calls.append(1)
            return original_inspect(target)

        monkeypatch.setattr(sqlalchemy, "inspect", counting_inspect)
        try:
            for index in range(6):
                audit.log("config_change", user_id=f"u{index}")
            assert audit.flush(5.0) is True
        finally:
            monkeypatch.undo()
        assert len(_db_rows(factory)) == 6
        assert len(inspect_calls) <= 2, "FK 内省必须按实例缓存"
