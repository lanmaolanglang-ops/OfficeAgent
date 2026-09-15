"""Runtime configuration loading, validation, and hot-update coverage."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace


def test_environment_yaml_and_merge_loading(tmp_path, monkeypatch):
    from office_agent.config_system.loaders import EnvLoader, YamlLoader, deep_merge

    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("DEBUG", "no")
    monkeypatch.setenv("MAX_FILE_SIZE", "2048")
    monkeypatch.setenv("ENABLE_RAG", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "present")
    monkeypatch.setenv("TASK_TIMEOUT", "not-an-int")
    loaded = EnvLoader.load()
    assert loaded["environment"] == "testing"
    assert loaded["debug"] is False
    assert loaded["storage"]["max_file_size"] == 2048
    assert loaded["enable_rag"] is True
    assert loaded["_has_openai_key"] is True
    assert "task_timeout" not in loaded.get("queue", {})
    assert deep_merge({"storage": {"type": "local", "size": 1}}, {"storage": {"size": 2}}) == {
        "storage": {"type": "local", "size": 2}
    }

    loader = YamlLoader(str(tmp_path))
    assert loader.load("missing.yaml") == {}
    fake_yaml = SimpleNamespace(
        dump=lambda data, stream, **_kwargs: json.dump(data, stream, ensure_ascii=False),
        safe_load=lambda stream: json.load(stream),
    )
    monkeypatch.setitem(sys.modules, "yaml", fake_yaml)
    loader.save({"models": [{"model_id": "local"}]}, "models.yaml")
    assert loader.load_models() == [{"model_id": "local"}]
    loader.save({"agents": [{"agent_name": "A"}]}, "agents.yaml")
    loader.save({"prompts": [{"name": "P"}]}, "prompts.yaml")
    loader.save({"skills": [{"skill_name": "S"}]}, "skills.yaml")
    loader.save({"workflows": [{"workflow_name": "W"}]}, "workflows.yaml")
    assert loader.load_agents()[0]["agent_name"] == "A"
    assert loader.load_prompts()[0]["name"] == "P"
    assert loader.load_skills()[0]["skill_name"] == "S"
    assert loader.load_workflows()[0]["workflow_name"] == "W"
    (tmp_path / "broken.yaml").write_text("[unterminated", encoding="utf-8")
    assert loader.load("broken.yaml") == {}


def test_configuration_validator_reports_cross_reference_and_schema_problems(tmp_path, monkeypatch):
    from office_agent.config_system.schemas import GlobalConfig
    from office_agent.config_system.validators import ConfigValidator, Severity, validate_all

    global_config = GlobalConfig(
        environment="production", debug=True,
        storage={"local_path": str(tmp_path / "missing")},
        logging={"level": "DEBUG"}, default_model="",
    )
    validator = ConfigValidator()
    validator.validate_global(global_config)
    validator.validate_models([
        {"model_id": "", "enabled": False},
        {"model_id": "same", "model_name": "One", "provider": "openai", "enabled": True,
         "api_key_env": "UNSET_TEST_MODEL_KEY"},
        {"model_id": "same", "model_name": "Bad", "provider": "invalid", "temperature": 8},
    ])
    validator.validate_agents([
        {"agent_name": "", "enabled": True},
        {"agent_name": "Writer", "model_priority": ["missing-model"], "timeout": 0},
    ], models=[{"model_id": "same"}], prompts=[])
    validator.validate_prompts([
        {"name": "draft", "version": "1", "content": "Hello {name}", "variables": []},
        {"name": "draft", "version": "1", "content": "", "variables": []},
    ])
    validator.validate_workflows([
        {"workflow_name": "empty", "steps": []},
        {"workflow_name": "broken", "steps": [
            {"step_id": "one", "agent": "Ghost", "on_error": "explode"},
        ]},
    ], agents=[{"agent_name": "Writer"}])
    assert validator.has_errors() is True
    assert any(issue.severity == Severity.WARNING for issue in validator.issues)
    summary = validator.summary()
    assert "\u914d\u7f6e\u6821\u9a8c\u5b8c\u6210" in summary
    assert "ERROR" in str(next(issue for issue in validator.issues if issue.severity == Severity.ERROR))

    passed, issues = validate_all(
        global_config,
        [{"model_id": "bad", "model_name": "Bad", "provider": "invalid", "enabled": True}],
        [], [], workflows=[{"workflow_name": "empty", "steps": []}], strict=True,
    )
    assert passed is False and issues


def test_config_manager_initialization_queries_and_hot_updates(tmp_path, monkeypatch):
    import office_agent.config_system.config_manager as manager_module

    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None
    manager = manager_module.ConfigManager(config_dir=str(tmp_path))
    assert manager.initialize(strict=False) is True
    assert manager.global_config.version == "0.52.0"
    assert manager.get_enabled_models()[0]["priority"] >= manager.get_enabled_models()[-1]["priority"]
    assert manager.get_models_by_provider("openai")
    assert manager.get_agent("WordAgent")["enabled"] is True
    assert manager.get_enabled_agents()
    assert "Word" in manager.get_agent_prompt("WordAgent")
    assert manager.get_agent_prompt("Unknown") == ""
    assert manager.get_prompt("missing") is None
    prompt = manager.get_prompt("ppt_generate")
    assert manager.get_prompt("ppt_generate", prompt["version"]) == prompt
    rendered = manager.get_prompt_content("ppt_generate", variables={"topic": "Quarterly Review"})
    assert "Quarterly Review" in rendered
    assert manager.get_prompt_content("missing") is None
    assert manager.list_prompt_versions("ppt_generate")
    assert manager.get_skill("\u8bba\u6587\u6392\u7248")
    assert manager.get_enabled_skills()
    assert manager.get_workflow("word_processing")
    assert manager.get_enabled_workflows()
    assert manager.get_model_for_agent("WordAgent") is not None
    assert manager.get_model_for_agent("missing") is not None

    changes = []
    manager.add_listener(lambda kind, key: changes.append((kind, key)))
    manager.add_listener(lambda *_args: (_ for _ in ()).throw(RuntimeError("listener")))
    assert manager.update_model("custom", {"provider": "custom", "enabled": True})["model_id"] == "custom"
    assert manager.update_agent("CustomAgent", {"enabled": False})["agent_name"] == "CustomAgent"
    new_prompt = manager.update_prompt("custom_prompt", "Hello {name}", version="2.0", set_default=True)
    assert new_prompt["is_default"] is True
    assert manager.update_skill("custom_skill", {"enabled": True})["skill_name"] == "custom_skill"
    assert manager.update_workflow("custom_flow", {"enabled": True})["workflow_name"] == "custom_flow"
    assert ("model", "custom") in changes
    exported = manager.export_all()
    assert exported["models"] and exported["loaded_at"] > 0

    cache = manager_module.ConfigCache()
    cache.set("key", "value")
    cache.loaded_at = 4
    assert cache.get("key") == "value" and cache.all() == {"key": "value"}
    cache.clear()
    assert cache.get("key") is None and cache.loaded_at == 0

    merged = manager_module._merge_list(
        [{"model_id": "a", "value": 1}],
        [{"model_id": "a", "extra": 2}, {"ignored": True}], "model_id",
    )
    assert merged == [{"model_id": "a", "value": 1, "extra": 2}]
    assert manager_module._model_to_db({"model_id": "a"})["model_name"] == "a"
    assert manager_module._agent_to_db({"agent_name": "A"})["version"] == "1.0.0"
    assert manager_module.get_config() is manager_module.get_config()
    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None


def test_database_loader_selects_enabled_and_full_collections(monkeypatch):
    from office_agent.config_system.loaders import DatabaseLoader
    import office_agent.database.repository as repository_module

    sessions = []

    class _Session:
        def __init__(self):
            self.closed = False
            sessions.append(self)

        def close(self):
            self.closed = True

    class _Repo:
        def __init__(self, _session):
            pass

        def get_enabled(self):
            return [SimpleNamespace(id="enabled")]

        def get_active(self):
            return [SimpleNamespace(id="active")]

        def find(self, **_kwargs):
            return [SimpleNamespace(id="all")]

        def to_dict(self, item):
            return {"id": item.id}

    for name in (
        "ModelConfigRepository", "AgentConfigRepository", "PromptConfigRepository",
        "SkillConfigRepository", "WorkflowConfigRepository",
    ):
        monkeypatch.setattr(repository_module, name, _Repo)
    loader = DatabaseLoader(_Session)
    assert loader.load_models() == [{"id": "enabled"}]
    assert loader.load_models(False) == [{"id": "all"}]
    assert loader.load_agents() == [{"id": "enabled"}]
    assert loader.load_agents(False) == [{"id": "all"}]
    assert loader.load_prompts() == [{"id": "active"}]
    assert loader.load_prompts(False) == [{"id": "all"}]
    assert loader.load_skills() == [{"id": "enabled"}]
    assert loader.load_skills(False) == [{"id": "all"}]
    assert loader.load_workflows() == [{"id": "enabled"}]
    assert loader.load_workflows(False) == [{"id": "all"}]
    assert all(session.closed for session in sessions)
