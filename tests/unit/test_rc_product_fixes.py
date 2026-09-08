"""Focused regression tests for the RC product-fix batch."""
import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{tmp_path / name}"


def test_runtime_migration_upgrades_fresh_database_to_head(tmp_path):
    from office_agent.database.runtime_migrations import (
        migration_head,
        upgrade_database,
    )

    url = _database_url(tmp_path, "fresh.db")
    assert upgrade_database(url) == migration_head()

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == migration_head()
            assert connection.execute(text(
                "SELECT id, parent_task_id, revision_number FROM task WHERE 1=0"
            )).all() == []
    finally:
        engine.dispose()


def test_runtime_migration_adopts_complete_unversioned_legacy_database(tmp_path):
    from office_agent.database.base import Base
    from office_agent.database import models  # noqa: F401
    from office_agent.database.models.task import Task
    from office_agent.database.runtime_migrations import (
        migration_head,
        upgrade_database,
    )

    url = _database_url(tmp_path, "legacy.db")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Task(
            id="legacy-task",
            task_type="word_format",
            status="waiting",
            instruction="legacy",
        ))
        session.commit()
    engine.dispose()

    assert upgrade_database(url) == migration_head()

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT status FROM task WHERE id='legacy-task'"
            )).scalar_one() == "queued"
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version"
            )).scalar_one() == migration_head()
    finally:
        engine.dispose()


def test_runtime_migration_seeds_rbac_when_legacy_timestamps_have_no_default(
        tmp_path):
    """Reproduce the schema shipped by the installed 0.51.1 desktop DB."""
    from office_agent.database.base import Base
    from office_agent.database import models  # noqa: F401
    from office_agent.database.runtime_migrations import (
        migration_head,
        upgrade_database,
    )

    url = _database_url(tmp_path, "legacy-rbac-timestamps.db")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table in ("security_permissions", "security_roles"):
            connection.execute(text(f"DROP TABLE {table}"))
        connection.execute(text("""
            CREATE TABLE security_permissions (
                id VARCHAR(32) PRIMARY KEY NOT NULL,
                name VARCHAR(128) NOT NULL UNIQUE,
                resource VARCHAR(64) NOT NULL,
                action VARCHAR(64) NOT NULL,
                description TEXT,
                risk_level VARCHAR(16),
                created_at DATETIME NOT NULL,
                updated_at DATETIME
            )
        """))
        connection.execute(text("""
            CREATE TABLE security_roles (
                id VARCHAR(32) PRIMARY KEY NOT NULL,
                name VARCHAR(64) NOT NULL UNIQUE,
                display_name VARCHAR(128),
                description TEXT,
                permissions JSON,
                is_system BOOLEAN,
                is_active BOOLEAN,
                created_at DATETIME NOT NULL,
                updated_at DATETIME
            )
        """))
    engine.dispose()

    assert upgrade_database(url) == migration_head()

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT COUNT(*) FROM security_permissions "
                "WHERE created_at IS NOT NULL"
            )).scalar_one() == 20
            assert connection.execute(text(
                "SELECT COUNT(*) FROM security_roles "
                "WHERE created_at IS NOT NULL"
            )).scalar_one() == 3
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version"
            )).scalar_one() == migration_head()
    finally:
        engine.dispose()


def test_runtime_migration_rejects_incomplete_unversioned_database(tmp_path):
    from office_agent.database.runtime_migrations import upgrade_database

    url = _database_url(tmp_path, "unrelated.db")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE task (id VARCHAR(32) PRIMARY KEY)"))
    engine.dispose()

    with pytest.raises(RuntimeError, match="missing tables"):
        upgrade_database(url)


def test_startup_migration_failure_prevents_worker_start(monkeypatch):
    from office_agent.api import main as main_module
    import office_agent.task_queue as task_queue

    worker_calls = []
    monkeypatch.setattr(
        main_module,
        "upgrade_database",
        lambda: (_ for _ in ()).throw(RuntimeError("migration failed")),
    )
    monkeypatch.setattr(
        task_queue, "init_worker", lambda: worker_calls.append(True)
    )

    app = main_module.create_app()
    asyncio.run(app.state.startup_handler())

    assert app.state.database_ready is False
    assert app.state.database_schema_revision is None
    assert app.state.worker_started is False
    assert worker_calls == []


def test_health_checks_require_real_lifecycle_state(monkeypatch):
    from office_agent.api.router import health as health_module
    import office_agent.task_queue as task_queue

    state = SimpleNamespace(database_ready=False, worker_started=False)
    monkeypatch.setattr(
        task_queue,
        "get_initialized_worker",
        lambda: (_ for _ in ()).throw(AssertionError("must not construct worker")),
    )

    assert health_module._check_database(state)["status"] == "unhealthy"
    assert health_module._check_schema(state)["status"] == "unhealthy"
    assert health_module._check_workers(state)["status"] == "unhealthy"


def test_worker_health_uses_initialized_running_instance(monkeypatch):
    from office_agent.api.router import health as health_module
    import office_agent.task_queue as task_queue

    worker = SimpleNamespace(
        is_running=lambda: True,
        get_active_count=lambda: 2,
    )
    monkeypatch.setattr(task_queue, "get_initialized_worker", lambda: worker)

    result = health_module._check_workers(SimpleNamespace(worker_started=True))
    assert result == {
        "status": "healthy",
        "active_tasks": 2,
        "message": "2个活跃任务",
    }


def test_unknown_component_status_is_not_overall_healthy():
    from office_agent.api.router.health import _get_overall_status

    assert _get_overall_status({"workers": {"status": "unknown"}}) == "degraded"


@pytest.mark.parametrize(
    ("ppt_result", "error_fragment"),
    [
        (None, "未返回结果"),
        (SimpleNamespace(success=False, message="planner failed"), "planner failed"),
        (
            SimpleNamespace(
                success=True,
                output_path="missing.pptx",
                slide_count=3,
                message="",
            ),
            "输出文件不存在",
        ),
    ],
)
def test_ppt_task_never_reports_success_without_registered_output(
        monkeypatch, tmp_path, ppt_result, error_fragment):
    from office_agent.task_queue.tasks import ppt_tasks
    import office_agent.model_gateway as model_gateway_module
    import office_agent.ppt_agent.ppt_orchestrator as orchestrator_module

    class FakeOrchestrator:
        def __init__(self, **_kwargs):
            self.image_generation = {}

        def generate_from_theme(self, **_kwargs):
            return ppt_result

    monkeypatch.setattr(
        ppt_tasks, "_understand_ppt_request", lambda instruction, options, progress: instruction
    )
    monkeypatch.setattr(
        model_gateway_module,
        "ModelGateway",
        lambda **_kwargs: SimpleNamespace(last_call=None),
    )
    monkeypatch.setattr(orchestrator_module, "PPTOrchestrator", FakeOrchestrator)

    result = ppt_tasks.generate_ppt(
        outline="季度复盘",
        output_path=str(tmp_path / "missing.pptx"),
        options={"generate_images": False},
    )

    assert result["status"] == "failed"
    assert result["output_files"] == []
    assert error_fragment in result["error"]


def test_ppt_task_reports_success_only_after_output_registration(monkeypatch, tmp_path):
    from office_agent.task_queue.tasks import ppt_tasks
    import office_agent.model_gateway as model_gateway_module
    import office_agent.ppt_agent.ppt_orchestrator as orchestrator_module
    import office_agent.storage.storage_service as storage_module

    output_path = tmp_path / "generated.pptx"
    output_path.write_bytes(b"pptx")

    class FakeOrchestrator:
        def __init__(self, **_kwargs):
            self.image_generation = {}

        def generate_from_theme(self, **_kwargs):
            return SimpleNamespace(
                success=True,
                output_path=str(output_path),
                slide_count=4,
                message="generated",
            )

    class FakeStorage:
        def save_new_output(self, **_kwargs):
            return SimpleNamespace(file_id="registered-ppt")

    monkeypatch.setattr(
        ppt_tasks, "_understand_ppt_request", lambda instruction, options, progress: instruction
    )
    monkeypatch.setattr(
        model_gateway_module,
        "ModelGateway",
        lambda **_kwargs: SimpleNamespace(last_call=None),
    )
    monkeypatch.setattr(orchestrator_module, "PPTOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(storage_module, "get_storage_service", FakeStorage)

    result = ppt_tasks.generate_ppt(
        outline="季度复盘",
        output_path=str(output_path),
        options={"generate_images": False},
    )

    assert result["status"] == "success"
    assert result["output_files"] == ["registered-ppt"]
    assert result["slides"] == 4
