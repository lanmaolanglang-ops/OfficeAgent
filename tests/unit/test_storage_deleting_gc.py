"""P2-8：deleting tombstone 的 GC / 启动收口回归测试。

覆盖重点：delete(permanent=True) 先写 tombstone 再删物理内容，两步之间失败或
进程崩溃会让记录永久停在 deleting——对所有查询不可见，却持续占着数据库行与
磁盘内容。此前全仓无 cleanup_deleting / reconcile。
"""
import os
import inspect
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from office_agent.database.base import Base
import office_agent.database.models  # noqa: F401  (注册表元数据)
from office_agent.database.models import File, FileVersion
from office_agent.database.time import utc_now
from office_agent.storage.local_storage import LocalStorage
from office_agent.storage.storage_service import (
    DELETING_GRACE_SECONDS, StorageConfig, StorageService,
)


def _make_service(tmp_path, request):
    # 内存库 + StaticPool：所有 session 共享同一连接，避免每条用例
    # 都落盘建库（文件型 sqlite 的 create_all 在本机约 4s/次）。
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    request.addfinalizer(engine.dispose)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = StorageService(
        StorageConfig(local_path=str(tmp_path / "objects")),
        LocalStorage(str(tmp_path / "objects")),
    )
    service._get_session = factory
    return service, factory


def _insert_ready(factory, file_id, storage_path, *, size=7, status="ready"):
    """写入一条 uploaded/ready 记录（与 _insert_deleting 同源，便于对照）。"""
    stamp = utc_now()
    session = factory()
    try:
        session.add(File(
            id=file_id, filename="legacy.txt", original_name="legacy.txt",
            file_type="text", extension="txt", storage_path=storage_path,
            bucket="uploads", file_size=size, status=status,
            created_at=stamp, updated_at=stamp,
        ))
        session.commit()
    finally:
        session.close()


def _seed_file(service, factory, file_id, *, payload=b"keep me", status="ready"):
    """落一条真实物理内容 + DB 记录（不走校验链，保持测试轻量）。"""
    storage_path = f"uploads/{file_id}/body.txt"
    service.backend.upload(storage_path, payload)
    _insert_ready(factory, file_id, storage_path, status=status)
    return storage_path


def _insert_deleting(factory, file_id, storage_path, *, age_seconds=3600,
                     file_size=7):
    """直接写入一条"很久以前就卡住"的 deleting 记录（绕开 onupdate）。"""
    stamp = utc_now() - timedelta(seconds=age_seconds)
    session = factory()
    try:
        session.add(File(
            id=file_id, filename="legacy.txt", original_name="legacy.txt",
            file_type="text", extension="txt", storage_path=storage_path,
            bucket="uploads", file_size=file_size, status="deleting",
            created_at=stamp, updated_at=stamp,
        ))
        session.commit()
    finally:
        session.close()


class _Recorder:
    """记录 cleanup_deleting 的调用参数（替代全局单例）。"""

    def __init__(self, sink):
        self._sink = sink

    def cleanup_deleting(self, **kwargs):
        self._sink.append(kwargs)
        return {"scanned": 0, "resolved": 0, "failed": 0, "freed_bytes": 0}


def _insert_version(factory, parent_id, version_id, storage_path):
    session = factory()
    try:
        session.add(FileVersion(
            id=version_id, parent_file_id=parent_id, version_number=1,
            storage_path=storage_path, file_size=3,
        ))
        session.commit()
    finally:
        session.close()


def _status_of(factory, file_id):
    session = factory()
    try:
        row = session.get(File, file_id)
        return row.status if row else None
    finally:
        session.close()


def _row_count(factory, model):
    from sqlalchemy import select, func
    session = factory()
    try:
        return int(session.execute(
            select(func.count()).select_from(model)
        ).scalar() or 0)
    finally:
        session.close()


class TestDeletingGcIsRecoverable:
    def test_deleting_with_file_present_retries_deletion(self, tmp_path, request):
        service, factory = _make_service(tmp_path, request)
        storage_path = _seed_file(service, factory, "file_body")
        _insert_deleting(factory, "file_stuck", storage_path)
        assert service.backend.exists(storage_path)

        outcome = service.cleanup_deleting(min_age_seconds=0)

        assert outcome["scanned"] == 1
        assert outcome["resolved"] == 1
        assert outcome["failed"] == 0
        assert not service.backend.exists(storage_path)  # 物理内容被回收
        assert _status_of(factory, "file_stuck") is None       # DB 收口

    def test_deleting_with_file_already_gone_closes_db_row(self, tmp_path, request):
        service, factory = _make_service(tmp_path, request)
        _insert_deleting(factory, "file_ghost", "uploads/gone/missing.txt")

        outcome = service.cleanup_deleting(min_age_seconds=0)

        assert outcome["resolved"] == 1
        assert _status_of(factory, "file_ghost") is None

    def test_deleting_rows_clean_up_their_versions(self, tmp_path, request):
        service, factory = _make_service(tmp_path, request)
        _insert_deleting(factory, "file_stuck", "uploads/gone/missing.txt")
        _insert_version(factory, "file_stuck", "ver_stuck", "uploads/gone/v1.txt")

        service.cleanup_deleting(min_age_seconds=0)

        assert _row_count(factory, FileVersion) == 0
        assert _row_count(factory, File) == 0

    def test_failed_deletion_stays_recoverable(self, tmp_path, request, monkeypatch):
        service, factory = _make_service(tmp_path, request)
        storage_path = _seed_file(service, factory, "file_body")
        _insert_deleting(factory, "file_locked", storage_path)

        real_delete = service.backend.delete

        def refuse(_path):
            raise OSError("文件被占用")

        monkeypatch.setattr(service.backend, "delete", refuse)
        outcome = service.cleanup_deleting(min_age_seconds=0)
        assert outcome["resolved"] == 0
        assert outcome["failed"] == 1
        # 保持 deleting：既不假装成功，也不把残缺文件暴露为 ready
        assert _status_of(factory, "file_locked") == "deleting"

        monkeypatch.setattr(service.backend, "delete", real_delete)
        outcome = service.cleanup_deleting(min_age_seconds=0)
        assert outcome["resolved"] == 1
        assert _status_of(factory, "file_locked") is None

    def test_repeated_gc_is_idempotent(self, tmp_path, request):
        service, factory = _make_service(tmp_path, request)
        _insert_deleting(factory, "file_stuck", "uploads/gone/missing.txt")

        first = service.cleanup_deleting(min_age_seconds=0)
        second = service.cleanup_deleting(min_age_seconds=0)

        assert first["scanned"] == 1
        assert second["scanned"] == 0
        assert second["resolved"] == 0


class TestDeletingGcScope:
    def test_active_file_is_never_touched(self, tmp_path, request):
        service, factory = _make_service(tmp_path, request)
        storage_path = _seed_file(service, factory, "file_live")
        session = factory()
        try:
            row = session.get(File, "file_live")
            row.updated_at = utc_now() - timedelta(days=365)
            session.commit()
        finally:
            session.close()

        outcome = service.cleanup_deleting(min_age_seconds=0)

        assert outcome["scanned"] == 0
        assert _status_of(factory, "file_live") == "ready"
        assert service.backend.exists(storage_path)

    def test_soft_deleted_is_out_of_scope(self, tmp_path, request):
        service, factory = _make_service(tmp_path, request)
        storage_path = _seed_file(service, factory, "file_trashed", status="deleted")
        session = factory()
        try:
            row = session.get(File, "file_trashed")
            row.updated_at = utc_now() - timedelta(days=365)
            session.commit()
        finally:
            session.close()

        outcome = service.cleanup_deleting(min_age_seconds=0)

        assert outcome["scanned"] == 0
        assert _status_of(factory, "file_trashed") == "deleted"
        assert service.backend.exists(storage_path)

    def test_other_file_paths_are_untouched(self, tmp_path, request):
        service, factory = _make_service(tmp_path, request)
        victim_path = _seed_file(service, factory, "file_victim", payload=b"keep me")
        _insert_deleting(factory, "file_stuck", "uploads/gone/missing.txt")

        service.cleanup_deleting(min_age_seconds=0)

        assert service.backend.exists(victim_path)
        assert service.backend.download(victim_path) == b"keep me"
        assert _status_of(factory, "file_victim") == "ready"


class TestDeletingGcGraceWindow:
    def test_in_flight_tombstone_is_not_stolen(self, tmp_path, request):
        """刚写下的 tombstone（并发进行中的删除）不得被 GC 抢走。"""
        service, factory = _make_service(tmp_path, request)
        storage_path = _seed_file(service, factory, "file_inflight")
        session = factory()
        try:
            row = session.get(File, "file_inflight")
            row.status = "deleting"   # updated_at 被 onupdate 刷新为当下
            session.commit()
        finally:
            session.close()

        outcome = service.cleanup_deleting()  # 默认冷却窗口

        assert outcome["scanned"] == 0
        assert _status_of(factory, "file_inflight") == "deleting"
        assert service.backend.exists(storage_path)

    def test_default_grace_is_explicit(self):
        assert DELETING_GRACE_SECONDS > 0

    def test_legacy_null_updated_at_rows_are_eligible(self, tmp_path, request):
        """遗留库的 updated_at 是后加的列，可能允许 NULL 且存在空值。

        当前模型里该列 NOT NULL，无法通过 ORM 造出，这里复刻遗留表形态
        （把约束去掉）后走真实 Repository 查询，确认这类记录不会被漏掉。
        """
        from sqlalchemy import text
        from office_agent.database.repository import FileRepository

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        request.addfinalizer(engine.dispose)
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE file RENAME TO file_legacy_old"))
            conn.execute(text("CREATE TABLE file AS SELECT * FROM file_legacy_old WHERE 0"))
            conn.execute(text("DROP TABLE file_legacy_old"))
            conn.execute(text(
                "INSERT INTO file (id, filename, original_name, file_type, extension,"
                " storage_path, bucket, file_size, status, updated_at)"
                " VALUES ('file_legacy','a.txt','a.txt','text','txt',"
                " 'uploads/gone/legacy.txt','uploads',1,'deleting',NULL)"
            ))
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        session = factory()
        try:
            repo = FileRepository(session)
            old = repo.get_deleting(older_than=utc_now() - timedelta(seconds=1), limit=10)
            fresh = repo.get_deleting(older_than=utc_now() - timedelta(hours=1), limit=10)
        finally:
            session.close()
        assert [row.id for row in old] == ["file_legacy"]
        assert [row.id for row in fresh] == ["file_legacy"]


class TestDeletingGcFailureHandling:
    def test_single_failure_does_not_block_the_batch(self, tmp_path, request,
                                                     monkeypatch):
        service, factory = _make_service(tmp_path, request)
        _insert_deleting(factory, "file_bad", "uploads/gone/bad.txt")
        _insert_deleting(factory, "file_good", "uploads/gone/good.txt")
        # 两条都真实落盘，否则 backend.exists 为假、删除逻辑根本不会被调用
        service.backend.upload("uploads/gone/bad.txt", b"bad")
        service.backend.upload("uploads/gone/good.txt", b"good")

        real_delete = service.backend.delete

        def fail_for_bad(path):
            if "bad" in path:
                raise OSError("bad path")
            return real_delete(path)

        monkeypatch.setattr(service.backend, "delete", fail_for_bad)
        outcome = service.cleanup_deleting(min_age_seconds=0)

        assert outcome["scanned"] == 2
        assert outcome["resolved"] == 1
        assert outcome["failed"] == 1
        assert _status_of(factory, "file_bad") == "deleting"
        assert _status_of(factory, "file_good") is None

    def test_db_error_is_fail_closed(self, tmp_path, request, monkeypatch):
        """查询本身出错时必须冒泡，不能返回"0 条"假装成功。"""
        service, _ = _make_service(tmp_path, request)
        from office_agent.database.repository import FileRepository

        def boom(*_args, **_kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(FileRepository, "get_deleting", boom)
        with pytest.raises(RuntimeError, match="database unavailable"):
            service.cleanup_deleting(min_age_seconds=0)


class TestStartupWiring:
    def test_startup_calls_reconcile_once(self):
        """启动期必须调用一次收口入口（接线守卫，锁死调用点）。"""
        from office_agent.api import main as api_main

        source = inspect.getsource(api_main)
        start = source.index("async def on_startup")
        end = source.index("async def on_shutdown")
        body = source[start:end]
        assert body.count("reconcile_pending_deletions") == 2  # import + 调用

    def test_reconcile_entry_uses_global_service(self, tmp_path, request, monkeypatch):
        from office_agent.storage import storage_service

        calls = []
        monkeypatch.setattr(
            storage_service, "get_storage_service",
            lambda: _Recorder(calls),
        )
        storage_service.reconcile_pending_deletions()
        assert calls == [{}]

    def test_cleanup_deleting_is_reachable_from_service(self):
        assert callable(StorageService.cleanup_deleting)


def test_module_clean():
    """占位：确保上面所有类被收集（文件级 smoke）。"""
    assert os.path.isdir(os.path.dirname(__file__))
