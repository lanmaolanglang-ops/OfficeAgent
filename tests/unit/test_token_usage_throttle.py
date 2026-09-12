"""P3-1：API Key 使用统计节流落库回归。

修复前：``verify_api_key`` 每次认证都 ``UPDATE last_used/use_count +
COMMIT``——认证路径上的逐请求写放大（会话/事务压力与请求延迟）。
语义确认：这两个字段是观测性数据，不参与认证判定、配额或计费，
允许节流聚合 + 关闭收尾；认证判定本身不得因统计写失败而失败。
"""
import threading

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from office_agent.database.base import Base
from office_agent.database import models  # noqa: F401  注册全部表
from office_agent.database.models.security import APIKeyModel
from office_agent.security.auth.token import (
    USAGE_FLUSH_INTERVAL_SECONDS,
    TokenManager,
    flush_all_token_usage,
)


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tokens.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _count_usage_writes(factory):
    """统计 security_api_keys 上的 UPDATE 语句（统计落库次数代理）。

    注意：语句经 .upper() 后比较，比较串必须全大写。
    """
    writes = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        if statement.upper().startswith("UPDATE SECURITY_API_KEYS"):
            writes.append(statement)

    event.listen(factory.kw["bind"], "before_cursor_execute", _record)
    return writes


class TestThrottledUsagePersistence:
    def test_first_verify_persists_then_throttles(self, factory):
        """首次使用立即落库；节流窗口内不再逐请求写库。"""
        manager = TokenManager(session_factory=factory, usage_flush_interval=3600)
        key = manager.create_api_key("user-1")
        assert manager.verify_api_key(key) is not None
        with factory() as session:
            row = session.scalar(select(APIKeyModel))
            assert row.last_used is not None
            assert row.use_count == 1

        for _ in range(20):
            assert manager.verify_api_key(key) is not None

        with factory() as session:
            row = session.scalar(select(APIKeyModel))
            assert row.use_count == 1, "节流窗口内不得逐请求写库"

    def test_window_writes_far_less_than_requests(self, factory):
        """N 次认证的统计落库次数必须显著小于 N。"""
        manager = TokenManager(session_factory=factory, usage_flush_interval=60)
        key = manager.create_api_key("user-1")
        writes = _count_usage_writes(factory)

        for _ in range(30):
            assert manager.verify_api_key(key) is not None

        assert len(writes) == 1, "30 次认证只应产生 1 次统计写"
        with factory() as session:
            row = session.scalar(select(APIKeyModel))
            assert row.use_count == 1  # 窗口内内存累计尚未落库

    def test_flush_persists_pending_counts(self, factory):
        manager = TokenManager(session_factory=factory, usage_flush_interval=3600)
        key = manager.create_api_key("user-1")
        manager.verify_api_key(key)
        for _ in range(9):
            manager.verify_api_key(key)

        assert manager.flush_usage() == 1
        with factory() as session:
            row = session.scalar(select(APIKeyModel))
            assert row.use_count == 10, "关闭冲洗必须把内存累计全部落库"
        assert manager.flush_usage() == 0, "重复冲洗是幂等 no-op"

    def test_flush_interval_zero_writes_every_request(self, factory):
        """间隔 0 = 每次都落库（显式退回旧行为的口径仍受支持）。"""
        manager = TokenManager(session_factory=factory, usage_flush_interval=0)
        key = manager.create_api_key("user-1")
        for _ in range(3):
            assert manager.verify_api_key(key) is not None
        with factory() as session:
            row = session.scalar(select(APIKeyModel))
            assert row.use_count == 3

    def test_concurrent_verifies_do_not_lose_counts(self, factory):
        """并发认证的内存累计不丢（最终 flush 总数 = 认证次数）。"""
        manager = TokenManager(session_factory=factory,
                               usage_flush_interval=3600)
        key = manager.create_api_key("user-1")
        total = 40
        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            for _ in range(total // 4):
                assert manager.verify_api_key(key) is not None

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        manager.flush_usage()
        with factory() as session:
            row = session.scalar(select(APIKeyModel))
            assert row.use_count == total, "并发计数不得丢失"


class TestShutdownFlush:
    def test_flush_all_reaches_registry_instances(self, factory):
        manager = TokenManager(session_factory=factory, usage_flush_interval=3600)
        key = manager.create_api_key("user-1")
        for _ in range(5):
            manager.verify_api_key(key)

        assert flush_all_token_usage() >= 1

        with factory() as session:
            row = session.scalar(select(APIKeyModel))
            assert row.use_count == 5

    def test_flush_all_survives_instance_failure(self, factory, monkeypatch):
        """单个实例冲洗失败只记录，不阻断其它实例收尾。"""
        good = TokenManager(session_factory=factory, usage_flush_interval=3600)
        broken = TokenManager(session_factory=factory, usage_flush_interval=3600)
        monkeypatch.setattr(broken, "flush_usage",
                            lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        key = good.create_api_key("user-1")
        good.verify_api_key(key)   # 首次立即落库
        good.verify_api_key(key)   # 第二次进入节流窗口的 pending

        assert flush_all_token_usage() >= 1
        with factory() as session:
            row = session.scalar(select(APIKeyModel))
            assert row.use_count == 2

    def test_default_interval_constant(self):
        assert USAGE_FLUSH_INTERVAL_SECONDS == 60.0
