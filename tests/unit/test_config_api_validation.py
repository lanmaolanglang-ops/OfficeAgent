"""P2-4：config 路由校验与读写同源回归。

修复前：路由请求模型不限制未知 key（pydantic 默认静默丢弃，PUT 一个
拼错的 key 也报成功）、数值无边界校验、provider 不做白名单校验；
模型配置的 GET/PUT 与网关真实消费的 models.json 不是同一来源。

修复后：extra="forbid" 拒绝未知 key、数值按 config 轨实际 schema 限定
边界、provider 经权威别名归一后必须落在 ModelProvider 枚举内、端点必须
为空串或 http(s) URL；GET/PUT/reload 与网关全部同源（models.json）。
"""
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import office_agent.config_system.config_manager as manager_module
from office_agent.api.router import config as config_router
from office_agent.api.router.config import (
    AgentUpdate,
    ModelUpdate,
    PromptCreate,
    SkillUpdate,
    WorkflowUpdate,
)
from office_agent.config_system.config_manager import ConfigManager
from office_agent.model_gateway.model_manager import ModelManager

PROVIDER_KEY_ENV_VARS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY", "DOUBAO_API_KEY",
)


@pytest.fixture
def api_manager(tmp_path, monkeypatch):
    for name in PROVIDER_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None
    store = ModelManager(config_dir=str(tmp_path / "models"))
    manager = ConfigManager(config_dir=str(tmp_path), model_store=store)
    manager.initialize(strict=False)
    monkeypatch.setattr(config_router, "get_config", lambda *a, **k: manager)
    yield manager
    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None


class TestSchemaValidation:
    def test_unknown_key_rejected(self):
        with pytest.raises(ValidationError):
            ModelUpdate(temperature=0.5, evil_extra="x")
        with pytest.raises(ValidationError):
            AgentUpdate(timeout=10, evil_extra="x")
        with pytest.raises(ValidationError):
            SkillUpdate(enabled=True, evil_extra="x")
        with pytest.raises(ValidationError):
            WorkflowUpdate(enabled=True, evil_extra="x")
        with pytest.raises(ValidationError):
            PromptCreate(name="n", content="c", evil_extra="x")

    def test_numeric_bounds_rejected(self):
        with pytest.raises(ValidationError):
            ModelUpdate(temperature=3)
        with pytest.raises(ValidationError):
            ModelUpdate(top_p=1.5)
        with pytest.raises(ValidationError):
            ModelUpdate(max_tokens=0)
        with pytest.raises(ValidationError):
            ModelUpdate(context_length=0)
        with pytest.raises(ValidationError):
            AgentUpdate(timeout=0)
        with pytest.raises(ValidationError):
            AgentUpdate(max_retries=-1)
        with pytest.raises(ValidationError):
            AgentUpdate(quality_threshold=1.2)
        with pytest.raises(ValidationError):
            WorkflowUpdate(timeout=0)

    def test_boundary_values_accepted(self):
        assert ModelUpdate(temperature=0, top_p=1, max_tokens=1).temperature == 0
        assert AgentUpdate(timeout=1, max_retries=0).timeout == 1
        assert AgentUpdate(quality_threshold=0).quality_threshold == 0
        assert AgentUpdate(quality_threshold=1).quality_threshold == 1

    def test_bad_endpoint_scheme_rejected(self):
        with pytest.raises(ValidationError):
            ModelUpdate(api_endpoint="ftp://example.com")
        with pytest.raises(ValidationError):
            ModelUpdate(api_endpoint="api.example.com/v1")
        # 空串表示回退 provider 默认端点，合法
        assert ModelUpdate(api_endpoint="").api_endpoint == ""
        assert ModelUpdate(api_endpoint="https://api.example.com/v1").api_endpoint

    def test_wrong_types_rejected(self):
        with pytest.raises(ValidationError):
            ModelUpdate(temperature="hot")
        with pytest.raises(ValidationError):
            ModelUpdate(tags="not-a-list")
        with pytest.raises(ValidationError):
            SkillUpdate(tools="not-a-list")
        with pytest.raises(ValidationError):
            PromptCreate(name="", content="x")
        with pytest.raises(ValidationError):
            PromptCreate(name="n", content="")


class TestProviderWhitelist:
    def test_unknown_provider_rejected(self, api_manager):
        with pytest.raises(HTTPException) as excinfo:
            config_router.update_model(
                "doubao-default", ModelUpdate(provider="bogus", temperature=0.5)
            )
        assert excinfo.value.status_code == 400

    def test_legacy_provider_alias_accepted(self, api_manager):
        response = config_router.update_model(
            "doubao-default", ModelUpdate(provider=" Anthropic ")
        )
        assert response["success"] is True

    def test_known_provider_accepted(self, api_manager):
        response = config_router.update_model(
            "doubao-default", ModelUpdate(provider="doubao")
        )
        assert response["success"] is True


class TestReadWriteSameSource:
    def test_put_get_reload_and_gateway_agree(self, api_manager):
        manager = api_manager
        response = config_router.update_model(
            "doubao-default", ModelUpdate(temperature=0.66, max_tokens=2048)
        )
        assert response["success"] is True

        # GET 与 PUT 同一来源
        listing = config_router.list_models(enabled_only=False)
        entry = next(m for m in listing["data"] if m["model_id"] == "doubao-default")
        assert entry["temperature"] == 0.66
        assert entry["max_tokens"] == 2048

        # 网关真实读取的 models.json 同步生效
        fresh = ModelManager(config_dir=manager._model_store.config_dir)
        assert fresh.get_model("doubao-default").temperature == 0.66

        # reload 后一致
        manager.reload()
        assert manager.get_model("doubao-default")["temperature"] == 0.66

    def test_agent_route_resolves_alias_and_reads_same_source(self, api_manager):
        response = config_router.update_agent("word_agent", AgentUpdate(timeout=88))
        assert response["data"]["agent_name"] == "WordAgent"
        assert config_router.get_agent("WordAgent")["data"]["timeout"] == 88
        assert config_router.get_agent("word_agent")["data"]["timeout"] == 88

    def test_model_listing_never_exposes_key_material(self, api_manager):
        listing = config_router.list_models(enabled_only=False)
        dumped = json.dumps(listing["data"], ensure_ascii=False)
        # "api_key"（密钥材料字段）不得出现；"api_key_env"（环境变量名）是既有公开字段
        assert '"api_key"' not in dumped
        assert "sk-" not in dumped

    def test_get_missing_model_still_404(self, api_manager):
        with pytest.raises(HTTPException) as excinfo:
            config_router.get_model("no-such-model")
        assert excinfo.value.status_code == 404
