"""File schema convergence contract tests.

权威契约：
- ``original_name`` 是用户可见原始文件名的唯一权威字段；``filename`` 是
  delegate-only 兼容列。
- ``storage_path`` 是持久化存储相对路径的唯一权威字段；``file_path`` 是
  delegate-only 兼容列。

覆盖：ORM 单写 delegate、legacy-only 行回填、冲突行 fail loudly、
NULL/empty、Unicode 文件名、Windows 分隔符、旧库 upgrade、
upgrade→downgrade→upgrade、真实文件系统旧行 → 迁移后仍可读同一文件。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "office_agent" / "database" / "alembic.ini"))
    cfg.set_main_option(
        "script_location",
        str(ROOT / "office_agent" / "database" / "migrations"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    cfg.config_file_name = None
    return cfg


def _fresh_session(engine):
    from office_agent.database.base import Base
    import office_agent.database.models  # noqa: F401

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class TestAuthoritativeContractSingleWriter:
    """生产写入路径只允许写权威字段，兼容列由 ORM delegate 派生。"""

    def test_create_file_writes_only_authoritative_fields(self):
        from office_agent.database.repository import FileRepository

        engine = create_engine("sqlite:///:memory:")
        factory = _fresh_session(engine)
        with factory() as session:
            db_file = FileRepository(session).create_file(
                original_name="季度报告.docx", file_type="word", extension=".docx",
                storage_path="uploads/2026/01/01/file_a.docx", file_size=10,
            )
            session.commit()
            # 兼容列被 delegate 为权威值，且不是独立写入的第二份值
            assert db_file.original_name == "季度报告.docx"
            assert db_file.filename == "季度报告.docx"
            assert db_file.file_path == "uploads/2026/01/01/file_a.docx"

    def test_repo_update_of_storage_path_delegates_file_path(self):
        from office_agent.database.repository import FileRepository

        engine = create_engine("sqlite:///:memory:")
        factory = _fresh_session(engine)
        with factory() as session:
            repo = FileRepository(session)
            db_file = repo.create_file(
                original_name="a.txt", file_type="text", extension=".txt",
                storage_path="uploads/old.txt", file_size=1,
            )
            repo.update(db_file.id, {"storage_path": "uploads/new.txt"})
            session.commit()
        with factory() as session:
            row = session.execute(text(
                "SELECT storage_path, file_path FROM file WHERE id=:fid"
            ), {"fid": db_file.id}).one()
            assert row.storage_path == "uploads/new.txt"
            assert row.file_path == "uploads/new.txt"

    def test_explicit_legacy_value_is_overridden_by_authority(self):
        """即使调用方试图给兼容列独立值，delegate 也会强制回权威值。"""
        from office_agent.database.models.file import File

        engine = create_engine("sqlite:///:memory:")
        factory = _fresh_session(engine)
        with factory() as session:
            session.add(File(
                id="file_x", filename="evil.bin", original_name="real.docx",
                file_type="word", extension=".docx",
                storage_path="uploads/real.docx", file_path="elsewhere.bin",
            ))
            session.commit()
        with factory() as session:
            row = session.execute(text(
                "SELECT filename, file_path FROM file WHERE id='file_x'"
            )).one()
            assert row.filename == "real.docx"
            assert row.file_path == "uploads/real.docx"

    def test_direct_orm_insert_without_legacy_columns(self):
        from office_agent.database.models.file import File

        engine = create_engine("sqlite:///:memory:")
        factory = _fresh_session(engine)
        with factory() as session:
            session.add(File(
                id="file_y", original_name="数据.xlsx", file_type="excel",
                extension=".xlsx", storage_path="uploads/数据.xlsx",
            ))
            session.commit()
            fetched = session.get(File, "file_y")
            assert fetched.filename == "数据.xlsx"
            assert fetched.file_path == "uploads/数据.xlsx"

    def test_task_router_reads_only_authoritative_name(self):
        from office_agent.api.router.task import _file_id_list_to_infos
        from types import SimpleNamespace

        db_file = SimpleNamespace(
            id="file_a", original_name="report.docx",
            filename="stored.docx", status="ready",
        )

        class _Files:
            def get_by_id(self, _file_id):
                return db_file

        infos = _file_id_list_to_infos(["file_a"], _Files())
        assert infos[0]["filename"] == "report.docx"


class TestProductionSourceGuards:
    """源码接线守卫：生产代码不得再独立写/读兼容列。"""

    def test_file_repo_has_no_legacy_column_writes(self):
        src = (ROOT / "office_agent" / "database" / "repository" / "file_repo.py").read_text(
            encoding="utf-8")
        assert "filename = original_name" not in src
        assert "file_path=storage_path" not in src

    def test_storage_service_has_no_legacy_path_writes(self):
        src = (ROOT / "office_agent" / "storage" / "storage_service.py").read_text(
            encoding="utf-8")
        assert '"file_path":' not in src

    def test_task_router_has_no_legacy_name_read(self):
        src = (ROOT / "office_agent" / "api" / "router" / "task.py").read_text(
            encoding="utf-8")
        assert "db_file.filename" not in src


def _seed_file_row(engine, **values):
    defaults = {
        "id": "file_seed", "filename": "seed.txt", "original_name": "seed.txt",
        "file_type": "text", "extension": ".txt",
        "storage_path": "uploads/2026/01/01/file_seed.txt",
        "file_path": "uploads/2026/01/01/file_seed.txt",
        "file_size": 3, "bucket": "uploads", "storage_backend": "local",
        "status": "ready", "version": 1,
    }
    defaults.update(values)
    columns = ", ".join(defaults)
    placeholders = ", ".join(f":{key}" for key in defaults)
    with engine.begin() as connection:
        connection.execute(
            text(f"INSERT INTO file ({columns}) VALUES ({placeholders})"), defaults)


def _file_row(engine, fid):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT filename, original_name, storage_path, file_path"
            " FROM file WHERE id=:fid"
        ), {"fid": fid}).mappings().one()


class TestFileSchemaConvergenceMigration:
    def test_legacy_rows_converge_and_upgrade_downgrade_upgrade(self, tmp_path):
        db_path = tmp_path / "files.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "010_config_json_columns")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")

        # 1) 正常旧行（两列同值，中文文件名）
        _seed_file_row(engine, id="file_cn", filename="季度报告.docx",
                       original_name="季度报告.docx",
                       storage_path="uploads/2026/01/01/file_cn.docx",
                       file_path="uploads/2026/01/01/file_cn.docx",
                       file_type="word", extension=".docx")
        # 2) legacy-only：file_path NULL（004 后兼容列不再写入）
        _seed_file_row(engine, id="file_null_legacy", file_path=None)
        # 3) legacy-only：filename 空串（手工/外部写入残留）
        _seed_file_row(engine, id="file_empty_filename", filename="")
        # 4) 权威列缺失、仅 legacy 有值（001 前手工数据恢复场景）
        _seed_file_row(engine, id="file_legacy_only", filename="旧名.txt",
                       original_name="", storage_path="",
                       file_path="uploads/2026/01/01/file_legacy_only.txt")
        # 5) 同名不同存储 identity 的两行
        _seed_file_row(engine, id="file_same_name_a", filename="report.txt",
                       original_name="report.txt",
                       storage_path="uploads/2026/01/01/file_same_name_a.txt",
                       file_path="uploads/2026/01/01/file_same_name_a.txt")
        _seed_file_row(engine, id="file_same_name_b", filename="report.txt",
                       original_name="report.txt",
                       storage_path="uploads/2026/01/02/file_same_name_b.txt",
                       file_path="uploads/2026/01/02/file_same_name_b.txt")
        # 6) 生成的 artifact（outputs bucket）
        _seed_file_row(engine, id="file_artifact", filename="输出.pptx",
                       original_name="输出.pptx",
                       storage_path="outputs/2026/01/01/file_artifact.pptx",
                       file_path="outputs/2026/01/01/file_artifact.pptx",
                       file_type="ppt", extension=".pptx", bucket="outputs")
        # 7) Windows 反斜杠分隔符历史路径（迁移不得改写，原样保留）
        _seed_file_row(engine, id="file_winsep", filename="w.txt",
                       original_name="w.txt",
                       storage_path="uploads\\2026\\01\\01\\file_winsep.txt",
                       file_path="uploads\\2026\\01\\01\\file_winsep.txt")

        command.upgrade(cfg, "head")

        row = _file_row(engine, "file_cn")
        assert row["filename"] == row["original_name"] == "季度报告.docx"

        row = _file_row(engine, "file_null_legacy")
        assert row["file_path"] == row["storage_path"]

        row = _file_row(engine, "file_empty_filename")
        assert row["filename"] == row["original_name"]

        row = _file_row(engine, "file_legacy_only")
        assert row["original_name"] == "旧名.txt"
        assert row["storage_path"] == "uploads/2026/01/01/file_legacy_only.txt"

        # 同名不同 identity 互不干扰
        assert _file_row(engine, "file_same_name_a")["storage_path"].endswith("_a.txt")
        assert _file_row(engine, "file_same_name_b")["storage_path"].endswith("_b.txt")

        # Windows 分隔符路径原样保留（不做第二套 normalization）
        assert _file_row(engine, "file_winsep")["storage_path"] == \
            "uploads\\2026\\01\\01\\file_winsep.txt"

        # downgrade 为安全 no-op：旧表示（含兼容列）仍可读且语义一致
        command.downgrade(cfg, "010_config_json_columns")
        row = _file_row(engine, "file_cn")
        assert row["filename"] == "季度报告.docx"
        assert row["file_path"] == row["storage_path"]

        # 再次 upgrade：幂等
        command.upgrade(cfg, "head")
        row = _file_row(engine, "file_cn")
        assert row["filename"] == row["original_name"] == "季度报告.docx"

        # 迁移后新 ORM/Repository 可读旧行
        from office_agent.database.models.file import File
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            assert session.get(File, "file_artifact").original_name == "输出.pptx"

    def test_conflicting_name_row_fails_loudly_with_context(self, tmp_path):
        db_path = tmp_path / "conflict-name.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "010_config_json_columns")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_file_row(engine, id="file_conflict", filename="甲.docx",
                       original_name="乙.docx")

        with pytest.raises(RuntimeError, match=r"file_conflict.*filename.*original_name"):
            command.upgrade(cfg, "head")

    def test_conflicting_path_row_fails_loudly_with_context(self, tmp_path):
        db_path = tmp_path / "conflict-path.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "010_config_json_columns")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_file_row(engine, id="file_path_conflict",
                       storage_path="uploads/a.txt", file_path="uploads/b.txt")

        with pytest.raises(RuntimeError, match=r"file_path_conflict.*storage_path.*file_path"):
            command.upgrade(cfg, "head")

    def test_row_without_any_path_fails_loudly(self, tmp_path):
        db_path = tmp_path / "no-path.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "010_config_json_columns")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_file_row(engine, id="file_no_path", storage_path="", file_path=None)

        with pytest.raises(RuntimeError, match=r"file_no_path.*不指向任何物理文件"):
            command.upgrade(cfg, "head")

    def test_row_without_any_name_fails_loudly(self, tmp_path):
        db_path = tmp_path / "no-name.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "010_config_json_columns")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_file_row(engine, id="file_no_name", filename="", original_name="")

        with pytest.raises(RuntimeError, match=r"file_no_name.*无法确定用户可见文件名"):
            command.upgrade(cfg, "head")


class TestRealFilesystemOldRowMigration:
    """旧 DB 行 + 实际旧文件 → 迁移 → 新代码仍能定位并读取同一个文件。"""

    def test_old_row_and_physical_file_survive_migration(self, tmp_path):
        storage_root = tmp_path / "storage"
        legacy_rel = "uploads/2026/01/01/file_legacy.docx"
        physical = storage_root / "uploads" / "2026" / "01" / "01" / "file_legacy.docx"
        physical.parent.mkdir(parents=True)
        physical.write_bytes("legacy-bytes-中文内容".encode("utf-8"))

        artifact_rel = "outputs/2026/01/02/file_out.pptx"
        artifact = storage_root / "outputs" / "2026" / "01" / "02" / "file_out.pptx"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"generated-artifact")

        db_path = tmp_path / "real.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "010_config_json_columns")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        # 旧表示：只有 file_path 的 legacy-only 行（模拟 001 时代经 004 backfill 前的样子，
        # 此处直接把 storage_path 置空验证恢复性回填 + 物理文件定位）
        _seed_file_row(engine, id="file_legacy", filename="旧文档.docx",
                       original_name="旧文档.docx", storage_path="",
                       file_path=legacy_rel, file_type="word", extension=".docx")
        # 正常旧行：generated artifact
        _seed_file_row(engine, id="file_out", filename="结果.pptx",
                       original_name="结果.pptx", storage_path=artifact_rel,
                       file_path=artifact_rel, file_type="ppt",
                       extension=".pptx", bucket="outputs")

        command.upgrade(cfg, "head")

        # 迁移后：legacy-only 行的权威路径已恢复，且仍指向同一个物理文件
        row = _file_row(engine, "file_legacy")
        assert row["storage_path"] == legacy_rel
        assert row["file_path"] == legacy_rel

        from office_agent.storage.local_storage import LocalStorage
        backend = LocalStorage(str(storage_root))
        assert backend.download(row["storage_path"]) == "legacy-bytes-中文内容".encode("utf-8")

        artifact_row = _file_row(engine, "file_out")
        assert backend.download(artifact_row["storage_path"]) == b"generated-artifact"

        # Windows 风格分隔符在 Windows 上经唯一 canonical 解析同样可定位
        from office_agent.storage.local_storage import LocalStorage as LS
        win_backend = LS(str(storage_root))
        resolved = win_backend._full_path(legacy_rel.replace("/", "\\"))
        assert Path(resolved).read_bytes() == "legacy-bytes-中文内容".encode("utf-8")

    def test_windows_separator_row_not_rewritten_by_migration(self, tmp_path):
        db_path = tmp_path / "winsep.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "010_config_json_columns")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        win_path = "uploads\\2026\\01\\01\\file_w.txt"
        _seed_file_row(engine, id="file_w", storage_path=win_path, file_path=win_path)
        command.upgrade(cfg, "head")
        assert _file_row(engine, "file_w")["storage_path"] == win_path


class TestFileVersionTableUnaffected:
    """FileVersion 只有单一 storage_path 权威列，收敛不触碰版本行。"""

    def test_version_rows_survive_migration(self, tmp_path):
        db_path = tmp_path / "versions.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "010_config_json_columns")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_file_row(engine, id="file_v")
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO file_version (id, parent_file_id, version_number,"
                " storage_path, file_size) VALUES"
                " ('ver_1', 'file_v', 1, 'uploads/2026/01/01/file_v.txt', 3)"
            ))
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            version_path = connection.execute(text(
                "SELECT storage_path FROM file_version WHERE id='ver_1'"
            )).scalar_one()
            assert version_path == "uploads/2026/01/01/file_v.txt"
