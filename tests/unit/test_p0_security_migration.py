import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _session_factory():
    from office_agent.database.base import Base
    from office_agent.database import models  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False), engine


def test_model_keys_use_random_master_key_and_migrate_xor(tmp_path):
    from office_agent.model_gateway.model_manager import ModelManager, SimpleEncryption

    legacy_ciphertext = SimpleEncryption().encrypt("legacy-secret")
    (tmp_path / "models.json").write_text(json.dumps({
        "models": [{
            "id": "openai-default", "provider": "openai",
            "api_key_enc": legacy_ciphertext,
        }],
        "routing": {},
    }), encoding="utf-8")

    manager = ModelManager(str(tmp_path))
    assert manager.get_model("openai-default").api_key == "legacy-secret"
    saved = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert saved["models"][0]["api_key_enc"].startswith("v2:")
    assert (tmp_path / "master.key").is_file()
    assert ModelManager(str(tmp_path)).get_model("openai-default").api_key == "legacy-secret"


def test_v2_master_key_moves_with_legacy_data_dir(tmp_path, monkeypatch):
    from office_agent.model_gateway.model_manager import ApiKeyCrypto, resolve_model_config_dir

    fake_home = tmp_path / "home"
    legacy = fake_home / ".office_agent"
    target = tmp_path / "new-data"
    legacy.mkdir(parents=True)
    target.mkdir()
    crypto = ApiKeyCrypto(legacy)
    (legacy / "models.json").write_text(json.dumps({
        "models": [{"api_key_enc": crypto.encrypt("secret")}]
    }), encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(target))

    assert resolve_model_config_dir() == target
    assert (target / "master.key").read_bytes() == (legacy / "master.key").read_bytes()
    assert (target / "models.json").is_file()


def test_gemini_model_key_is_header_only(monkeypatch):
    from office_agent.model_gateway.clients import gemini_client as module
    from office_agent.model_gateway.clients.gemini_client import GeminiClient
    from office_agent.models.model_schemas import ModelConfig, ModelProvider

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        return Response()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    client = GeminiClient(ModelConfig(
        id="g", provider=ModelProvider.GEMINI, display_name="g", api_key="secret-key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-test",
    ))
    assert client.chat([{"role": "user", "content": "hi"}]).success
    assert "secret-key" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "secret-key"


def test_api_keys_are_database_authoritative_and_hashed():
    from office_agent.database.models import APIKeyModel
    from office_agent.security.auth.token import TokenManager

    factory, _ = _session_factory()
    key = TokenManager(session_factory=factory).create_api_key("user-1", role="admin")
    with factory() as session:
        stored = session.scalar(select(APIKeyModel))
        assert stored.key_hash != key
        assert key not in stored.key_hash
    verified = TokenManager(session_factory=factory).verify_api_key(key)
    assert verified.user_id == "user-1"
    assert verified.role == "admin"
    assert TokenManager(session_factory=factory).revoke_api_key(key)
    assert TokenManager(session_factory=factory).verify_api_key(key) is None


def test_audit_is_persisted_and_retention_is_enforced():
    from office_agent.database.models import AuditLogModel
    from office_agent.security.audit import AuditLogger

    factory, _ = _session_factory()
    audit = AuditLogger(session_factory=factory)
    audit.log("config_change", user_id="external-user", resource="settings")
    with factory() as session:
        row = session.scalar(select(AuditLogModel))
        assert row.user_id is None
        assert row.details["subject_user_id"] == "external-user"
        row.timestamp = datetime.now(timezone.utc) - timedelta(days=100)
        session.commit()
    assert audit.cleanup_expired(90) == 1


def test_critical_foreign_keys_and_time_columns_are_explicit():
    from office_agent.database.models import (
        APIKeyModel, AuditLogModel, ExecutionLog, File, FileVersion, ModelCallLog, Task,
    )

    expectations = [
        (Task.parent_task_id, "SET NULL"), (Task.user_id, "SET NULL"),
        (File.owner_id, "SET NULL"), (File.parent_file_id, "SET NULL"),
        (FileVersion.parent_file_id, "CASCADE"), (ExecutionLog.task_id, "CASCADE"),
        (ModelCallLog.task_id, "CASCADE"), (APIKeyModel.user_id, "CASCADE"),
        (AuditLogModel.user_id, "SET NULL"),
    ]
    for column, expected in expectations:
        assert next(iter(column.foreign_keys)).ondelete == expected
    for column in (
        Task.started_at, Task.finished_at, File.deleted_at, File.last_accessed_at,
        ExecutionLog.start_time, ExecutionLog.end_time, AuditLogModel.timestamp,
        APIKeyModel.expires_at,
    ):
        assert column.type.timezone is True


def test_security_migration_upgrades_and_rolls_back(tmp_path):
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "migration.db"
    cfg = Config(str(root / "office_agent" / "database" / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(root / "office_agent" / "database" / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(cfg, "006_prompt_version_uniqueness")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE security_users (id VARCHAR(32) PRIMARY KEY, username VARCHAR(64), "
            "email VARCHAR(128), password_hash VARCHAR(256), role VARCHAR(32), "
            "is_active BOOLEAN, is_verified BOOLEAN, last_login DATETIME, "
            "last_login_ip VARCHAR(64), failed_login_count INTEGER, locked_until DATETIME, "
            "extra JSON, created_at DATETIME, updated_at DATETIME)"
        ))
        connection.execute(text(
            "INSERT INTO security_users VALUES "
            "('legacy-user','legacy',NULL,'hash','user',1,1,NULL,NULL,0,NULL,NULL,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO task (id, task_type, status, instruction) "
            "VALUES ('legacy-waiting', 'general', 'waiting', 'legacy')"
        ))
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert connection.execute(text(
            'SELECT username FROM "user" WHERE id="legacy-user"'
        )).scalar_one() == "legacy"
        assert "security_users" not in inspect(connection).get_table_names()
        assert "is_verified" in {c["name"] for c in inspect(connection).get_columns("user")}
        assert set(connection.execute(text(
            "SELECT name FROM security_roles"
        )).scalars()) >= {"admin", "user", "guest"}
        assert connection.execute(text(
            "SELECT COUNT(*) FROM security_permissions"
        )).scalar_one() >= 20
        assert connection.execute(text(
            "SELECT status FROM task WHERE id='legacy-waiting'"
        )).scalar_one() == "queued"
    command.downgrade(cfg, "006_prompt_version_uniqueness")
    assert "security_users" in inspect(engine).get_table_names()
    assert "is_verified" not in {c["name"] for c in inspect(engine).get_columns("user")}
    assert not inspect(engine).get_foreign_keys("model_call_log")
    parent_fk = next(
        fk for fk in inspect(engine).get_foreign_keys("task")
        if "parent_task_id" in fk.get("constrained_columns", [])
    )
    assert parent_fk["name"] == "fk_task_parent_task_id"
    assert not parent_fk.get("options")
    command.downgrade(cfg, "base")
    assert "security_users" not in inspect(engine).get_table_names()
