"""Config JSON Text -> native JSON migration and round-trip tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "office_agent" / "database" / "alembic.ini"))
    cfg.set_main_option(
        "script_location",
        str(ROOT / "office_agent" / "database" / "migrations"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    # Avoid Alembic's fileConfig side effect during the full test suite.
    cfg.config_file_name = None
    return cfg


def _seed_legacy_rows(engine) -> None:
    from office_agent.database.base import Base
    import office_agent.database.models  # noqa: F401

    metadata = Base.metadata
    with engine.begin() as connection:
        connection.execute(metadata.tables["model_config"].insert().values(
            id="mdl-legacy",
            model_id="legacy-model",
            model_name="Legacy Model",
            provider="custom",
            tags=json.dumps(["中文标签", "nested"]),
            config_json=json.dumps({"nested": {"enabled": True}}),
        ))
        connection.execute(metadata.tables["agent_runtime_config"].insert().values(
            id="agc-legacy",
            agent_name="LegacyAgent",
            model_priority=json.dumps(["model-a", "model-b"]),
            retry_config=json.dumps({"max_retries": 3}),
            tags=json.dumps(["agent"]),
            config_json=json.dumps({"extra": {"x": 1}}),
        ))
        connection.execute(metadata.tables["skill_config"].insert().values(
            id="skc-legacy",
            skill_name="LegacySkill",
            workflow=json.dumps([{"step": "run"}]),
            parameters=json.dumps({"timeout": 10}),
            tags=json.dumps(["skill"]),
            config_json=json.dumps({"extra": {}}),
        ))
        connection.execute(metadata.tables["workflow_config"].insert().values(
            id="wfc-legacy",
            workflow_name="LegacyWorkflow",
            steps=json.dumps(["step1", "step2"]),
            input_schema=json.dumps({"type": "object"}),
            output_schema=json.dumps({"type": "object"}),
            tags=json.dumps([]),
            config_json=json.dumps({}),
        ))
        connection.execute(metadata.tables["file_rule_config"].insert().values(
            id="frc-legacy",
            rule_name="legacy-rule",
            file_pattern="*.txt",
            actions=json.dumps(["allow"]),
            parameters=json.dumps({"max": 1}),
            config_json=json.dumps({}),
        ))

        # double-encoded historical representation
        connection.execute(text(
            "UPDATE model_config SET config_json=:value WHERE id='mdl-legacy'"
        ), {"value": json.dumps(json.dumps({"nested": {"enabled": True}}))})

        # empty string and whitespace must normalize to defaults
        connection.execute(text(
            "UPDATE agent_runtime_config SET tags=:value WHERE id='agc-legacy'"
        ), {"value": "  "})


def _assert_upgraded_orm_objects(engine) -> None:
    import office_agent.database.models  # noqa: F401
    from office_agent.database.models.config import (
        AgentConfigModel, ModelConfig, SkillConfigModel,
    )

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        model = session.get(ModelConfig, "mdl-legacy")
        assert model.tags == ["中文标签", "nested"]
        assert model.config_json == {"nested": {"enabled": True}}

        agent = session.get(AgentConfigModel, "agc-legacy")
        assert agent.model_priority == ["model-a", "model-b"]
        assert agent.retry_config == {"max_retries": 3}
        assert agent.tags == []

        skill = session.get(SkillConfigModel, "skc-legacy")
        assert skill.workflow == [{"step": "run"}]
        assert skill.parameters == {"timeout": 10}


class TestConfigJSONModelContract:
    def test_native_json_round_trip_with_nested_unicode(self):
        from office_agent.database.base import Base
        import office_agent.database.models  # noqa: F401
        from office_agent.database.models.config import ModelConfig
        from office_agent.database.repository import ModelConfigRepository

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(ModelConfig(
                id="mdl-json",
                model_id="json-model",
                model_name="JSON Model",
                provider="custom",
                tags=["中文", "标签"],
                config_json={"nested": {"enabled": True}},
            ))
            session.commit()

        with factory() as session:
            item = session.get(ModelConfig, "mdl-json")
            assert item.tags == ["中文", "标签"]
            assert item.config_json == {"nested": {"enabled": True}}
            assert ModelConfigRepository(session).to_dict(item)["tags"] == item.tags
            assert ModelConfigRepository(session).to_dict(item)["extra"] == item.config_json

    def test_defaults_are_python_containers(self):
        from office_agent.database.base import Base
        import office_agent.database.models  # noqa: F401
        from office_agent.database.models.config import PromptConfig

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            prompt = PromptConfig(id="prm-default", name="prompt", content="hello")
            session.add(prompt)
            session.flush()
            assert prompt.variables == []
            assert prompt.tags == []
            assert prompt.config_json == {}


class TestConfigJSONMigrationRoundTrip:
    def test_upgrade_downgrade_upgrade_with_legacy_rows(self, tmp_path):
        db_path = tmp_path / "config-json.db"
        cfg = _alembic_config(db_path)

        command.upgrade(cfg, "009_normalize_task_status")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_legacy_rows(engine)

        command.upgrade(cfg, "head")
        _assert_upgraded_orm_objects(engine)

        inspector = inspect(engine)
        for table, fields in (
            ("model_config", ("tags", "config_json")),
            ("agent_runtime_config", ("model_priority", "retry_config")),
        ):
            columns = {column["name"]: column for column in inspector.get_columns(table)}
            for field in fields:
                assert str(columns[field]["type"]).lower().startswith("json")

        command.downgrade(cfg, "009_normalize_task_status")
        with engine.connect() as connection:
            raw = connection.execute(text(
                "SELECT config_json FROM model_config WHERE id='mdl-legacy'"
            )).scalar_one()
            assert isinstance(raw, str)
            assert json.loads(raw) == {"nested": {"enabled": True}}

        command.upgrade(cfg, "head")
        _assert_upgraded_orm_objects(engine)

    def test_malformed_legacy_json_fails_with_row_context(self, tmp_path):
        db_path = tmp_path / "config-json-malformed.db"
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "009_normalize_task_status")
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        _seed_legacy_rows(engine)
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE skill_config SET parameters=:value WHERE id='skc-legacy'"
            ), {"value": "{not-json"})

        with pytest.raises(RuntimeError, match="skill_config.*skc-legacy.*parameters"):
            command.upgrade(cfg, "head")
