"""模型配置双轨收敛的专项回归。

覆盖：
- provider 枚举唯一（config_system 与 model_gateway 共用权威枚举）
- 默认模型清单由权威源渲染，两条轨道读到同一份清单
- 历史 provider 别名（anthropic/ollama）与历史模型 ID 的兼容归一化
- Agent model_priority 统一为权威 ID，且能解析到默认清单中的模型
- ModelManager 环境变量加载与权威 PROVIDER_API_KEY_ENV 一致
- 用户已有 models.json（自定义 ID/Key）不受默认清单收敛影响
"""
import json

import pytest


def test_single_canonical_provider_enum():
    from office_agent.config_system import schemas as config_schemas
    from office_agent.models.model_schemas import ModelProvider

    # config_system 不再自带第二份枚举
    assert config_schemas.ModelProvider is ModelProvider
    assert {p.value for p in ModelProvider} == {
        "doubao", "openai", "claude", "gemini",
        "deepseek", "qwen", "agnes", "custom",
    }


def test_provider_alias_normalization():
    from office_agent.models.model_schemas import (
        ModelProvider, normalize_provider,
    )

    assert normalize_provider("anthropic") == "claude"
    assert normalize_provider(" Anthropic ") == "claude"
    assert normalize_provider("ollama") == "custom"
    assert normalize_provider("openai") == "openai"
    # 未知值原样返回，交给校验层报错
    assert normalize_provider("bogus") == "bogus"
    # 归一化结果都可构造权威枚举
    for alias in ("anthropic", "ollama", "doubao"):
        assert ModelProvider(normalize_provider(alias))


def test_default_catalog_matches_canonical_templates():
    from office_agent.models.model_schemas import (
        DEFAULT_MODEL_CONFIGS, PROVIDER_API_KEY_ENV, default_model_catalog,
    )

    catalog = default_model_catalog()
    assert len(catalog) == len(DEFAULT_MODEL_CONFIGS)
    by_id = {item["model_id"]: item for item in catalog}
    for provider, config in DEFAULT_MODEL_CONFIGS.items():
        item = by_id[config.id]
        assert item["provider"] == provider.value
        assert item["model_name"] == config.display_name
        assert item["api_endpoint"] == config.base_url
        assert item["api_key_env"] == PROVIDER_API_KEY_ENV.get(provider)
        assert item["max_tokens"] == config.max_tokens
        assert item["supports_vision"] == config.supports_vision
    # config 轨 priority 语义为“越大越优先”，换算后顺序与权威模板一致
    ordered = sorted(catalog, key=lambda item: item["priority"], reverse=True)
    canonical_order = sorted(
        DEFAULT_MODEL_CONFIGS.values(), key=lambda cfg: cfg.priority
    )
    assert [item["model_id"] for item in ordered] == [
        cfg.id for cfg in canonical_order
    ]


def test_legacy_model_id_aliases():
    from office_agent.models.model_schemas import (
        LEGACY_MODEL_ID_ALIASES, DEFAULT_MODEL_CONFIGS, normalize_model_id,
    )

    canonical_ids = {cfg.id for cfg in DEFAULT_MODEL_CONFIGS.values()}
    # 每个别名都指向真实存在的权威 ID
    for legacy, canonical in LEGACY_MODEL_ID_ALIASES.items():
        assert canonical in canonical_ids
        assert normalize_model_id(legacy) == canonical
    # 权威 ID 与未知 ID 原样透传
    assert normalize_model_id("doubao-default") == "doubao-default"
    assert normalize_model_id("my-custom-model") == "my-custom-model"


@pytest.fixture()
def fresh_config_manager(tmp_path):
    import office_agent.config_system.config_manager as manager_module

    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None
    manager = manager_module.ConfigManager(config_dir=str(tmp_path))
    manager.initialize(strict=False)
    yield manager
    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None


def test_config_track_defaults_come_from_canonical(fresh_config_manager):
    from office_agent.models.model_schemas import DEFAULT_MODEL_CONFIGS

    manager = fresh_config_manager
    canonical_ids = {cfg.id for cfg in DEFAULT_MODEL_CONFIGS.values()}
    assert canonical_ids <= set(manager._models)
    # 旧影子 ID 不再出现在默认清单
    for legacy_id in ("doubao-pro", "gpt-4o", "claude-3-5-sonnet", "qwen-max"):
        assert legacy_id not in manager._models


def test_config_manager_resolves_legacy_ids(fresh_config_manager):
    manager = fresh_config_manager
    legacy = manager.get_model("doubao-pro")
    canonical = manager.get_model("doubao-default")
    assert legacy is not None and legacy is canonical
    assert manager.get_model("gpt-4o")["provider"] == "openai"
    # 历史 provider 名查询也能命中
    assert manager.get_models_by_provider("anthropic") == \
        manager.get_models_by_provider("claude")


def test_agent_model_priority_uses_canonical_ids(fresh_config_manager):
    manager = fresh_config_manager
    for agent_name in ("WordAgent", "PPTAgent", "ExcelAgent"):
        agent = manager.get_agent(agent_name)
        assert agent["model_priority"], agent_name
        resolved = manager.get_model_for_agent(agent_name)
        assert resolved is not None, agent_name
        assert resolved["model_id"] == agent["model_priority"][0]
    # 全局默认模型必须是可解析的权威 ID
    assert manager.global_config.default_model == "doubao-default"
    assert manager.get_model(manager.global_config.default_model) is not None


def test_legacy_agent_config_still_resolves(fresh_config_manager):
    """用户 DB 中引用旧模型 ID 的 Agent 配置经兼容层仍可解析。"""
    manager = fresh_config_manager
    manager._agents["LegacyAgent"] = {
        "agent_name": "LegacyAgent",
        "model_priority": ["doubao-pro", "gpt-4o"],
        "enabled": True,
    }
    resolved = manager.get_model_for_agent("LegacyAgent")
    assert resolved is not None
    assert resolved["model_id"] == "doubao-default"


def test_update_model_normalizes_legacy_id(fresh_config_manager):
    manager = fresh_config_manager
    updated = manager.update_model("doubao-pro", {"temperature": 0.5})
    assert updated["model_id"] == "doubao-default"
    assert "doubao-pro" not in manager._models
    assert manager.get_model("doubao-default")["temperature"] == 0.5


def test_legacy_provider_record_passes_validation(fresh_config_manager):
    """含旧 provider 名（anthropic）的 DB 记录加载后归一化且不报 schema 错误。"""
    from office_agent.config_system.validators import ConfigValidator, Severity

    manager = fresh_config_manager
    manager._models["claude-default"] = {
        "model_id": "claude-default",
        "model_name": "Claude 3.5 Sonnet",
        "provider": "anthropic",  # 历史记录
        "enabled": True,
    }
    validator = ConfigValidator()
    validator.validate_models(list(manager._models.values()))
    schema_errors = [
        issue for issue in validator.issues
        if issue.severity == Severity.ERROR and "配置无效" in issue.message
    ]
    assert schema_errors == []


def test_model_manager_env_keys_follow_canonical_mapping(tmp_path, monkeypatch):
    from office_agent.model_gateway.model_manager import ModelManager
    from office_agent.models.model_schemas import (
        DEFAULT_MODEL_CONFIGS, PROVIDER_API_KEY_ENV,
    )

    monkeypatch.setenv("DOUBAO_API_KEY", "test-doubao-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    manager = ModelManager(config_dir=str(tmp_path))
    doubao = manager.get_model("doubao-default")
    gemini = manager.get_model("gemini-default")
    assert doubao is not None and doubao.api_key == "test-doubao-key"
    assert gemini is not None and gemini.api_key == "test-gemini-key"
    # 映射覆盖所有非 custom 的权威 provider
    assert set(PROVIDER_API_KEY_ENV) == set(DEFAULT_MODEL_CONFIGS)


def test_existing_user_models_json_untouched(tmp_path):
    """默认清单收敛不影响用户已有 models.json 中的自定义模型与密文 Key。"""
    from office_agent.model_gateway.model_manager import ModelManager

    config_dir = tmp_path / "cfg"
    manager = ModelManager(config_dir=str(config_dir))
    from office_agent.models.model_schemas import ModelConfig, ModelProvider

    custom = ModelConfig(
        id="my-custom",
        provider=ModelProvider.CUSTOM,
        display_name="My Custom",
        api_key="sk-user-secret",
        base_url="https://llm.example.com/v1",
        model="my-model",
    )
    manager.add_model(custom)
    saved = json.loads((config_dir / "models.json").read_text(encoding="utf-8"))
    entry = next(m for m in saved["models"] if m["id"] == "my-custom")
    assert entry["api_key_enc"].startswith("v2:")

    # 重新加载：自定义模型与 Key 原样回来
    reloaded = ModelManager(config_dir=str(config_dir))
    restored = reloaded.get_model("my-custom")
    assert restored is not None
    assert restored.api_key == "sk-user-secret"
    assert restored.model == "my-model"


def test_legacy_provider_in_models_json_normalized(tmp_path):
    """models.json 中的历史 provider 名读取时归一化，配置不丢失。"""
    from office_agent.model_gateway.model_manager import (
        ModelManager, ApiKeyCrypto,
    )

    config_dir = tmp_path / "cfg"
    config_dir.mkdir(parents=True)
    crypto = ApiKeyCrypto(config_dir)
    (config_dir / "models.json").write_text(json.dumps({
        "models": [{
            "id": "claude-default",
            "provider": "anthropic",
            "display_name": "Claude",
            "api_key_enc": crypto.encrypt("sk-ant-test"),
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude-3-5-sonnet-20241022",
        }],
        "routing": {},
        "default_model_id": "claude-default",
    }), encoding="utf-8")

    manager = ModelManager(config_dir=str(config_dir))
    model = manager.get_model("claude-default")
    assert model is not None
    assert model.provider.value == "claude"
    assert model.api_key == "sk-ant-test"
    assert manager.get_client("claude-default") is not None
