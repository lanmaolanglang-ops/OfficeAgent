"""P5-7：repository 事务语义与失败口径统一回归。

修复前：
- ``ModelConfigRepository/AgentConfigRepository.upsert`` 与
  ``PromptConfigRepository.set_default`` 在仓库内部**自 commit**——与
  BaseRepository 全家的 flush-only 契约相悖：调用方（如
  config_manager._persist_*）跨仓库的写入被"upsert 即提交"打断，
  repo A 提交 → repo B 失败 → 业务动作半成功且无法回滚；
- ``ExecutionLogRepository.get_errors/get_stats`` 的失败口径只认字面
  ``"error"``——而任务层真实写入的失败终态还有
  ``"failed"/"cancelled"``，统计漏计；与 ModelCallLog 的
  ``!= "success"`` 口径不一致。

事务测试使用真实 SQLite（内存库 + 独立连接），不 mock Session.commit。
"""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from office_agent.database.base import Base
from office_agent.database import models  # noqa: F401  注册全部表
from office_agent.database.models.config import ModelConfig
from office_agent.database.models.execution import ExecutionLog
from office_agent.database.repository.config_repo import ModelConfigRepository
from office_agent.database.repository.execution_repo import (
    FAILED_EXECUTION_STATUSES,
    ExecutionLogRepository,
)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class TestUpsertTransactionOwnership:
    def test_upsert_does_not_commit(self, session_factory):
        """回归核心：upsert 不得在仓库内部提交事务。"""
        session = session_factory()
        try:
            repo = ModelConfigRepository(session)
            repo.upsert("m1", {"model_name": "M1", "provider": "openai"})
            # 修复前：upsert 内部已 commit；修复后：仍在本事务内未落库
            assert session.in_transaction(), \
                "upsert 只允许 flush，事务必须留给调用方"
        finally:
            session.rollback()
            session.close()

    def test_caller_commit_persists(self, session_factory):
        """调用方 commit 后数据才真正持久化（flush 保证约束/ID 已生效）。"""
        session = session_factory()
        try:
            repo = ModelConfigRepository(session)
            repo.upsert("m1", {"model_name": "M1", "provider": "openai"})
            session.commit()
        finally:
            session.close()

        check = session_factory()
        try:
            row = check.execute(
                select(ModelConfig).where(ModelConfig.model_id == "m1")
            ).scalar_one()
            assert row.model_name == "M1"
        finally:
            check.close()

    def test_upsert_update_path_leaves_commit_to_caller(self, session_factory):
        """更新分支同样只 flush：两次 upsert 在同一事务内原子生效。"""
        session = session_factory()
        try:
            repo = ModelConfigRepository(session)
            repo.upsert("m1", {"model_name": "M1", "provider": "openai"})
            repo.upsert("m1", {"model_name": "M1-renamed"})
            # 第二次 upsert 若仍自 commit，这里的事务状态就是"已提交"
            assert session.in_transaction()
            session.commit()

            other = session_factory()
            try:
                row = other.execute(
                    select(ModelConfig).where(ModelConfig.model_id == "m1")
                ).scalar_one()
                assert row.model_name == "M1-renamed"
            finally:
                other.close()
        finally:
            session.rollback()
            session.close()

    def test_failure_after_upsert_rolls_back_everything(self, session_factory):
        """回归核心：upsert 后另一步失败 → 全部回滚，不留半成功。"""
        session = session_factory()
        try:
            repo = ModelConfigRepository(session)
            repo.upsert("m1", {"model_name": "M1", "provider": "openai"})
            # 模拟同一事务内第二步失败（触发约束错误）
            session.add(ModelConfig(model_id=None, model_name="bad"))
            with pytest.raises(Exception):
                session.flush()
            session.rollback()
        finally:
            session.close()

        check = session_factory()
        try:
            assert check.execute(
                select(ModelConfig)
            ).scalars().all() == [], "回滚后不得留下 upsert 半成品"
        finally:
            check.close()


class TestFailureStatusClassification:
    def _insert(self, session, status):
        session.add(ExecutionLog(
            id=f"log_{status}", agent="word", action="act",
            status=status, duration_ms=10,
        ))
        session.commit()

    def test_failed_and_cancelled_counted_as_errors(self, session_factory):
        """回归核心：failed/cancelled 是失败终态，统计不得漏计。"""
        session = session_factory()
        try:
            repo = ExecutionLogRepository(session)
            for status in ("error", "failed", "cancelled", "success"):
                self._insert(session, status)
        finally:
            session.close()

        check = session_factory()
        try:
            repo = ExecutionLogRepository(check)
            assert len(repo.get_errors(limit=100)) == 3
            stats = repo.get_stats(hours=24)
            assert stats["errors"] == 3, \
                "失败口径必须涵盖 error/failed/cancelled"
        finally:
            check.close()

    def test_success_not_counted(self, session_factory):
        session = session_factory()
        try:
            repo = ExecutionLogRepository(session)
            self._insert(session, "success")
        finally:
            session.close()

        check = session_factory()
        try:
            repo = ExecutionLogRepository(check)
            assert repo.get_errors() == []
            assert repo.get_stats(hours=24)["errors"] == 0
        finally:
            check.close()

    def test_failed_status_collection_is_shared_vocabulary(self):
        assert FAILED_EXECUTION_STATUSES == ("error", "failed", "cancelled")
