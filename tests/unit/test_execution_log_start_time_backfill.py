"""P2-3：execution_log.start_time 收紧 NOT NULL 前的历史回填。

004 把 start_time 先按 nullable 加列（或直接沿用旧库已有的 nullable 列），
随后立刻 alter_column(nullable=False)。旧库里这一列对该表已有行全为 NULL，
没有回填步骤会直接让 migration 失败（SQLite batch 会重建表并触发 NOT NULL
约束）。本文件锁定"先回填、再收紧"的契约。
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEGACY_REVISION = "003_file_deleted_at"


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "office_agent" / "database" / "alembic.ini"))
    cfg.set_main_option(
        "script_location",
        str(ROOT / "office_agent" / "database" / "migrations"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    cfg.config_file_name = None
    return cfg


def _seed_execution_log(engine, log_id: str, *, created_at: str | None,
                        start_time: str | None = "__absent__",
                        with_start_column: bool = False):
    """在 003 时代的 execution_log 上插一行。

    with_start_column=True 时模拟"列已存在但值为 NULL"的旧库。
    """
    if with_start_column:
        columns = "id, agent, created_at, updated_at, start_time"
        values = ":id, 'word', :created_at, :updated_at, :start_time"
    else:
        columns = "id, agent, created_at, updated_at"
        values = ":id, 'word', :created_at, :updated_at"
    with engine.begin() as connection:
        connection.execute(
            text(f"INSERT INTO execution_log ({columns}) VALUES ({values})"),
            {
                "id": log_id,
                "created_at": created_at,
                "updated_at": created_at,
                "start_time": None if start_time == "__absent__" else start_time,
            },
        )


def _start_time_column(engine):
    columns = inspect(engine).get_columns("execution_log")
    return next(c for c in columns if c["name"] == "start_time")


def _start_time_of(engine, log_id: str):
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT start_time FROM execution_log WHERE id=:id"), {"id": log_id}
        ).scalar()


class TestFreshDatabase:
    def test_brand_new_database_upgrades_and_marks_not_null(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        command.upgrade(_alembic_config(db_path), "head")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        assert _start_time_column(engine)["nullable"] is False


class TestLegacyRowsWithoutStartColumn:
    def test_missing_column_is_backfilled_before_enforcement(self, tmp_path):
        """旧库根本没这列：加列后全是 NULL，必须先回填再收紧。"""
        db_path = tmp_path / "no-column.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, LEGACY_REVISION)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_execution_log(engine, "log_legacy", created_at="2026-01-02 03:04:05.000000")

        command.upgrade(cfg, "head")

        assert _start_time_column(engine)["nullable"] is False
        # 回填来源必须是该行自己的创建时间，而不是迁移当下时间
        assert _start_time_of(engine, "log_legacy") == "2026-01-02 03:04:05.000000"

    def test_multiple_legacy_rows_keep_their_own_times(self, tmp_path):
        db_path = tmp_path / "many.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, LEGACY_REVISION)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_execution_log(engine, "log_a", created_at="2025-05-01 10:00:00.000000")
        _seed_execution_log(engine, "log_b", created_at="2025-06-02 11:30:00.000000")

        command.upgrade(cfg, "head")

        assert _start_time_of(engine, "log_a") == "2025-05-01 10:00:00.000000"
        assert _start_time_of(engine, "log_b") == "2025-06-02 11:30:00.000000"


class TestLegacyRowsWithNullableStartColumn:
    def test_null_values_are_backfilled(self, tmp_path):
        """列已存在且值为 NULL（旧库手工加列的形态）。"""
        db_path = tmp_path / "null-column.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, LEGACY_REVISION)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE execution_log ADD COLUMN start_time DATETIME"
            ))
        _seed_execution_log(engine, "log_null", created_at="2024-12-31 23:59:59.000000",
                            with_start_column=True)

        command.upgrade(cfg, "head")

        assert _start_time_column(engine)["nullable"] is False
        assert _start_time_of(engine, "log_null") == "2024-12-31 23:59:59.000000"

    def test_existing_non_null_values_are_untouched(self, tmp_path):
        """已有合法值不得被改写。"""
        db_path = tmp_path / "kept.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, LEGACY_REVISION)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE execution_log ADD COLUMN start_time DATETIME"
            ))
        _seed_execution_log(engine, "log_kept", created_at="2024-01-01 00:00:00.000000",
                            start_time="2023-07-07 07:07:07.000000",
                            with_start_column=True)

        command.upgrade(cfg, "head")

        assert _start_time_of(engine, "log_kept") == "2023-07-07 07:07:07.000000"

    def test_partially_present_companion_columns(self, tmp_path):
        """只有部分新列存在的旧库同样能通过（其余列仍按缺失处理）。"""
        db_path = tmp_path / "partial.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, LEGACY_REVISION)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE execution_log ADD COLUMN start_time DATETIME"
            ))
            connection.execute(text(
                "ALTER TABLE execution_log ADD COLUMN request_id VARCHAR(64)"
            ))
        _seed_execution_log(engine, "log_partial", created_at="2024-03-03 03:03:03.000000",
                            with_start_column=True)

        command.upgrade(cfg, "head")

        assert _start_time_of(engine, "log_partial") == "2024-03-03 03:03:03.000000"
        columns = {c["name"] for c in inspect(engine).get_columns("execution_log")}
        assert {"start_time", "request_id", "trace_id", "end_time"} <= columns


class TestMigrationIsRepeatable:
    def test_upgrade_downgrade_upgrade_cycle(self, tmp_path):
        db_path = tmp_path / "cycle.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, LEGACY_REVISION)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_execution_log(engine, "log_cycle", created_at="2025-01-01 00:00:00.000000")

        command.upgrade(cfg, "head")
        command.downgrade(cfg, LEGACY_REVISION)
        command.upgrade(cfg, "head")

        assert _start_time_column(engine)["nullable"] is False
        # downgrade 会丢列重建，二次 upgrade 后该行仍满足 NOT NULL
        with engine.connect() as connection:
            remaining = connection.execute(
                text("SELECT start_time FROM execution_log WHERE id='log_cycle'")
            ).fetchall()
        for row in remaining:
            assert row[0] is not None

    def test_second_upgrade_is_a_no_op(self, tmp_path):
        db_path = tmp_path / "noop.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "head")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        before = _start_time_column(engine)
        command.upgrade(cfg, "head")
        after = _start_time_column(engine)
        # SQLAlchemy 类型实例没有 __eq__（按身份比较），含 type 的整 dict
        # 直接比较恒为 False；按字段比较，type 用 repr 比对。
        assert {k: v for k, v in after.items() if k != "type"} == \
            {k: v for k, v in before.items() if k != "type"}
        assert repr(after["type"]) == repr(before["type"])


class TestBackfillSourceIsSemantic:
    def test_row_without_any_timestamp_still_satisfies_not_null(self, tmp_path):
        """连 created_at/updated_at 都没有的行也必须能被收口，且不能伪造随机值。"""
        db_path = tmp_path / "no-time.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, LEGACY_REVISION)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO execution_log (id, agent) VALUES ('log_notime', 'word')"
            ))

        command.upgrade(cfg, "head")

        assert _start_time_column(engine)["nullable"] is False
        assert _start_time_of(engine, "log_notime") is not None

    def test_updated_at_is_used_when_created_at_missing(self, tmp_path):
        db_path = tmp_path / "updated-only.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, LEGACY_REVISION)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        with engine.begin() as connection:
            # created_at 必须显式置 NULL：SQLite 只在列被省略时才应用
            # DEFAULT CURRENT_TIMESTAMP，省略写法会让 created_at 被自动
            # 填成迁移当下的时间，测不出"缺失时回落到 updated_at"。
            connection.execute(text(
                "INSERT INTO execution_log (id, agent, created_at, updated_at)"
                " VALUES ('log_upd', 'word', NULL, '2025-08-08 08:08:08.000000')"
            ))

        command.upgrade(cfg, "head")

        assert _start_time_of(engine, "log_upd") == "2025-08-08 08:08:08.000000"


def test_legacy_rows_survive_the_not_null_migration(tmp_path):
    """端到端：旧行不会因为收紧 NOT NULL 而丢失。"""
    db_path = tmp_path / "survive.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, LEGACY_REVISION)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    _seed_execution_log(engine, "log_1", created_at="2025-02-02 02:02:02.000000")
    _seed_execution_log(engine, "log_2", created_at="2025-03-03 03:03:03.000000")

    command.upgrade(cfg, "head")

    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT COUNT(*) FROM execution_log")
        ).scalar()
    assert count == 2
