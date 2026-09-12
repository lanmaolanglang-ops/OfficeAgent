"""P2-2：模型配置单一权威源（ModelManager / models.json）回归。

修复前：`/api/config/models` PUT 把配置写进数据库 model_config 表——一个
没有任何生产读者的第二权威源（网关、健康检查、设置接口全部以
ModelManager 的 models.json 为准，health.py 明文声明"以 ModelManager /
models.json 实际配置为准"）。两边互不知晓，必然发散。

修复后：注入 model_store 时新写入只进 models.json；数据库 model_config
表降级为旧数据读取兜底，加载顺序 defaults < YAML < DB 兜底 < 权威存储；
api_key 等密钥材料绝不进入 config 轨。
"""
import json

import pytest

import office_agent.config_system.config_manager as manager_module
from office_agent.config_system.config_manager import (
    ConfigManager,
    _candidate_to_store_entry,
    _model_store_entry_to_dict,
)
from office_agent.model_gateway.model_manager import ModelManager
from office_agent.models.model_schemas import (
    ModelConfig as GatewayModelConfig,
    ModelProvider,
)

PROVIDER_KEY_ENV_VARS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY", "DOUBAO_API_KEY",
)


@pytest.fixture
def reset_singleton():
    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None
    yield
    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None


@pytest.fixture
def store_manager(tmp_path, monkeypatch, reset_singleton):
    """注入权威模型存储的 ConfigManager；models.json 与密钥材料都在 tmp 内。"""
    for name in PROVIDER_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    store = ModelManager(config_dir=str(tmp_path / "models"))
    manager = ConfigManager(config_dir=str(tmp_path), model_store=store)
    manager.initialize(strict=False)
    return manager


def _db_loader_factory(rows):
    """替代 DatabaseLoader 的桩类工厂：load_models 注入旧行，其余为空。"""

    class _Loader:
        def __init__(self, session_factory):
            pass

        def load_models(self, only_enabled=True):
            return list(rows)

        def load_agents(self, only_enabled=True):
            return []

        def load_prompts(self, only_active=True):
            return []

        def load_skills(self, only_enabled=True):
            return []

        def load_workflows(self, only_enabled=True):
            return []

    return _Loader


class TestCanonicalOverride:
    def test_store_entry_overrides_default_catalog(self, store_manager):
        manager = store_manager
        manager._model_store.add_model(GatewayModelConfig(
            id="doubao-default", provider=ModelProvider.DOUBAO,
            display_name="豆包（用户配置）", temperature=0.55,
        ))
        manager.reload()
        assert manager.get_model("doubao-default")["temperature"] == 0.55
        assert manager.get_model("doubao-default")["model_name"] == "豆包（用户配置）"

    def test_legacy_db_rows_rank_below_canonical_store(
        self, store_manager, monkeypatch
    ):
        """同一模型 ID 两边都有数据时，权威存储优先，DB 只是兜底。"""
        manager = store_manager
        manager._model_store.add_model(GatewayModelConfig(
            id="doubao-default", provider=ModelProvider.DOUBAO,
            display_name="豆包（用户配置）", temperature=0.55,
        ))
        monkeypatch.setattr(
            manager_module, "DatabaseLoader",
            _db_loader_factory([{
                "model_id": "doubao-default", "model_name": "DB 旧值",
                "provider": "doubao", "temperature": 0.11, "enabled": True,
            }]),
        )
        manager._session_factory = object()
        manager.reload()
        assert manager.get_model("doubao-default")["temperature"] == 0.55
        assert manager.get_model("doubao-default")["model_name"] == "豆包（用户配置）"

    def test_db_only_legacy_rows_still_readable(self, store_manager, monkeypatch):
        """旧表独有的记录仍可读取（legacy read fallback），不丢数据。"""
        manager = store_manager
        monkeypatch.setattr(
            manager_module, "DatabaseLoader",
            _db_loader_factory([{
                "model_id": "legacy-only", "model_name": "旧表专属",
                "provider": "custom", "enabled": True,
            }]),
        )
        manager._session_factory = object()
        manager.reload()
        assert manager.get_model("legacy-only")["model_name"] == "旧表专属"


class TestSingleWriteTarget:
    def test_update_writes_only_to_canonical_store(self, store_manager, monkeypatch):
        manager = store_manager
        db_persist_calls = []
        monkeypatch.setattr(
            ConfigManager, "_persist_model",
            lambda self, mid, data: db_persist_calls.append(mid),
        )

        updated = manager.update_model("doubao-default", {"temperature": 0.42})

        assert updated["temperature"] == 0.42
        assert db_persist_calls == []  # 不再写第二权威源
        # 网关真实读取的 models.json 已生效
        fresh = ModelManager(config_dir=manager._model_store.config_dir)
        assert fresh.get_model("doubao-default").temperature == 0.42
        # reload 后读回一致
        manager.reload()
        assert manager.get_model("doubao-default")["temperature"] == 0.42

    def test_update_creates_new_model_in_canonical_store(self, store_manager):
        manager = store_manager
        updated = manager.update_model(
            "my-model",
            {"provider": "custom", "model_name": "我的模型", "temperature": 0.5},
        )
        assert updated["model_id"] == "my-model"
        fresh = ModelManager(config_dir=manager._model_store.config_dir)
        entry = fresh.get_model("my-model")
        assert entry is not None
        assert entry.display_name == "我的模型"
        assert entry.temperature == 0.5
        assert entry.api_key == ""  # 经 config 轨创建的条目不携带密钥材料

    def test_store_failure_leaves_memory_untouched(self, store_manager):
        manager = store_manager
        before = dict(manager.get_model("doubao-default"))

        class _BoomStore:
            def get_model(self, mid):
                return None

            def add_model(self, entry):
                raise OSError("disk full")

            def list_models(self):
                return []

        manager._model_store = _BoomStore()
        with pytest.raises(RuntimeError):
            manager.update_model("doubao-default", {"temperature": 0.99})
        assert manager.get_model("doubao-default") == before


class TestSecretMaterial:
    def test_api_key_never_enters_config_track(self, store_manager):
        manager = store_manager
        manager._model_store.add_model(GatewayModelConfig(
            id="doubao-default", provider=ModelProvider.DOUBAO,
            display_name="豆包", api_key="sk-secret-123",
        ))
        manager.reload()
        exported = json.dumps(manager.export_all(), ensure_ascii=False)
        assert "sk-secret-123" not in exported
        assert "api_key" not in manager.get_model("doubao-default")


class TestPrioritySemantics:
    def test_store_to_config_track_conversion(self):
        """config 轨 priority 越大越优先，与 default_model_catalog 同一换算。"""
        entry = GatewayModelConfig(
            id="m", provider=ModelProvider.OPENAI, display_name="M", priority=10,
        )
        assert _model_store_entry_to_dict(entry)["priority"] == 90

    def test_config_track_to_store_round_trip(self):
        class _OneEntryStore:
            def __init__(self, entry):
                self.entry = entry

            def get_model(self, mid):
                return self.entry if mid == self.entry.id else None

            def add_model(self, value):
                self.entry = value

        store = _OneEntryStore(GatewayModelConfig(
            id="m", provider=ModelProvider.OPENAI, display_name="M", priority=10,
        ))
        out = _candidate_to_store_entry(store, "m", {"priority": 90})
        assert out.priority == 10  # 反向换算后回到存储轨语义
        assert out.temperature == 0.3  # 未提供的字段保留存储条目原值
