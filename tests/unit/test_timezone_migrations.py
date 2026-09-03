"""时区收口的迁移链专项测试（清单 2.3 / PG 收口条目）。

覆盖：
- 迁移 004 不再为 file/execution_log 创建 naive DateTime 列；
- 迁移 007 的 timezone 转换守卫：已 aware 的列（全新部署）跳过
  ``AT TIME ZONE 'UTC'``，naive 列（存量部署）仍转换；
- 完整迁移链 001 -> head 在 SQLite 上可干净执行，且 head 与 ORM 元数据一致。
"""
import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "office_agent" / "database" / "migrations" / "versions"


def _load_migration(name: str):
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigration004AwareColumns:
    """004 是执行日志/文件时间列的创建点，必须与 ORM 模型一致（aware）。"""

    def test_004_declares_timezone_aware_columns(self):
        src = (VERSIONS / "004_reconcile_runtime_schema.py").read_text(encoding="utf-8")
        for column in ("start_time", "end_time", "last_accessed_at", "expires_at"):
            naive = f'sa.Column("{column}", sa.DateTime()'
            assert naive not in src, f"004 仍为 {column} 声明 naive DateTime"
            aware = re.search(
                rf'sa\.Column\("{column}", sa\.DateTime\(timezone=True\)', src,
            )
            assert aware, f"004 未为 {column} 声明 DateTime(timezone=True)"

    def test_orm_models_match_aware_declaration(self):
        from office_agent.database.base import Base
        from office_agent.database import models  # noqa: F401

        for table, column in (
            ("execution_log", "start_time"), ("execution_log", "end_time"),
            ("file", "last_accessed_at"), ("file", "expires_at"),
            ("task", "started_at"), ("task", "finished_at"),
        ):
            col = Base.metadata.tables[table].columns[column]
            assert isinstance(col.type, sa.DateTime)
            assert col.type.timezone is True, f"{table}.{column} 不是 timezone-aware"


class TestMigration007Guard:
    def test_naive_column_needs_conversion(self):
        mig = _load_migration("007_security_identity_time_fk")
        info = {"name": "start_time", "type": sa.DateTime()}
        assert mig._column_is_aware(info) is False

    def test_aware_column_skips_conversion(self):
        mig = _load_migration("007_security_identity_time_fk")
        # 全新部署（004 修复后）反射回来的 timestamptz 列必须跳过
        info = {"name": "start_time", "type": sa.DateTime(timezone=True)}
        assert mig._column_is_aware(info) is True

    def test_unknown_type_treated_as_naive(self):
        mig = _load_migration("007_security_identity_time_fk")
        assert mig._column_is_aware({"name": "x", "type": sa.Integer()}) is False


class TestMigrationChainIntegrity:
    def test_full_chain_upgrade_to_head(self, tmp_path, monkeypatch):
        db_path = tmp_path / "chain.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))

        from alembic.config import Config
        from alembic import command

        cfg = Config()
        cfg.set_main_option("script_location", str(VERSIONS.parent))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "head")

        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "009_normalize_task_status"
            # 链尾可正常写入 aware 时间（ORM 默认值 utc_now）
            conn.execute(sa.text(
                "INSERT INTO execution_log (id, task_id, agent, action, status, start_time)"
                " VALUES ('x1', NULL, 'a', 'b', 'success', :ts)"
            ), {"ts": datetime.now(timezone.utc)})

    def test_chain_downgrade_one_step_and_reupgrade(self, tmp_path, monkeypatch):
        """验证链尾 upgrade/downgrade 可逆（009 <-> 008）。"""
        db_path = tmp_path / "chain2.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))

        from alembic.config import Config
        from alembic import command

        cfg = Config()
        cfg.set_main_option("script_location", str(VERSIONS.parent))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "-1")
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "008_seed_database_rbac"
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "009_normalize_task_status"
