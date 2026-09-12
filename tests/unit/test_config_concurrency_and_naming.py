"""P2-5：配置并发安全与 Agent 命名归一回归。

修复前：模块级 `_config_lock` 只保护单例创建，`update_model` 等热更新的
读-改-写完全无锁——同 key 并发更新互相覆盖丢字段；`_rebuild_cache` 先
清空再逐个填充，并发读者可见空缓存；`agent_name` 的别名形态
（word_agent/WordAgent）在缓存与数据库里各成一份（命名多轨）。
"""
import threading
import time

import pytest

import office_agent.config_system.config_manager as manager_module
from office_agent.config_system.config_manager import (
    ConfigManager,
    _agent_match_key,
)


@pytest.fixture
def fresh_manager(tmp_path):
    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None
    manager = ConfigManager(config_dir=str(tmp_path))
    manager.initialize(strict=False)
    yield manager
    manager_module.ConfigManager._instance = None
    manager_module._config_manager = None


def _db_loader_factory(agent_rows):
    class _Loader:
        def __init__(self, session_factory):
            pass

        def load_models(self, only_enabled=True):
            return []

        def load_agents(self, only_enabled=True):
            return list(agent_rows)

        def load_prompts(self, only_active=True):
            return []

        def load_skills(self, only_enabled=True):
            return []

        def load_workflows(self, only_enabled=True):
            return []

    return _Loader


class TestConcurrency:
    def test_concurrent_same_key_updates_keep_every_field(
        self, fresh_manager, monkeypatch
    ):
        """同 key 并发热更新不得丢字段（lost update）。"""
        manager = fresh_manager
        first_entered = {"fired": False}

        def slow_first_persist(self, model_id, data):
            # 旧实现（无锁）：其余线程在这 0.25s 内全部基于同一旧状态完成
            # 读-改-写，最后只有一个字段幸存。
            # 新实现（锁内）：其余线程被挡在候选计算之前，逐个串行合并。
            if not first_entered["fired"]:
                first_entered["fired"] = True
                time.sleep(0.25)

        monkeypatch.setattr(ConfigManager, "_persist_model", slow_first_persist)
        fields = {"temperature": 0.11, "max_tokens": 2222, "top_p": 0.33,
                  "priority": 7}
        threads = [
            threading.Thread(target=manager.update_model,
                             args=("doubao-default", {key: value}))
            for key, value in fields.items()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        entry = manager.get_model("doubao-default")
        for key, value in fields.items():
            assert entry[key] == value, f"并发更新丢失了字段 {key}"

    def test_listener_failure_does_not_block_other_threads(self, fresh_manager):
        """监听器抛错只被吞掉（既有语义），不影响更新本身与锁的释放。"""
        manager = fresh_manager
        manager.add_listener(lambda *_: (_ for _ in ()).throw(RuntimeError("x")))
        updated = manager.update_model("doubao-default", {"temperature": 0.2})
        assert updated["temperature"] == 0.2
        assert manager.get_model("doubao-default")["temperature"] == 0.2


class TestAgentNamingCanonicalization:
    def test_alias_reads_hit_same_entry(self, fresh_manager):
        manager = fresh_manager
        assert manager.get_agent("word_agent") is manager.get_agent("WordAgent")
        assert manager.get_agent("WORD-AGENT") is manager.get_agent("WordAgent")
        assert manager.get_agent("word-agent")["timeout"] == \
            manager.get_agent("WordAgent")["timeout"]

    def test_alias_update_merges_into_canonical_entry(self, fresh_manager):
        manager = fresh_manager
        names_before = set(manager._agents)
        updated = manager.update_agent("word_agent", {"timeout": 55})
        assert updated["agent_name"] == "WordAgent"
        assert set(manager._agents) == names_before  # 不为别名新增条目
        assert manager.get_agent("WordAgent")["timeout"] == 55

    def test_alias_persist_targets_canonical_name(self, fresh_manager, monkeypatch):
        manager = fresh_manager
        persisted = []
        monkeypatch.setattr(
            ConfigManager, "_persist_agent",
            lambda self, name, data: persisted.append(name),
        )
        manager.update_agent("word_agent", {"timeout": 66})
        assert persisted == ["WordAgent"]  # 别名不会写进第二行

    def test_alias_rows_from_db_collapse_into_one(
        self, fresh_manager, monkeypatch
    ):
        """旧库里别名形态的行与默认大驼峰条目折叠为同一权威条目。"""
        manager = fresh_manager
        monkeypatch.setattr(
            manager_module, "DatabaseLoader",
            _db_loader_factory([{
                "agent_name": "word_agent", "timeout": 77, "enabled": True,
                "description": "来自旧别名行",
            }]),
        )
        manager._session_factory = object()
        manager.reload()
        collapsed = [n for n in manager._agents
                     if _agent_match_key(n) == "wordagent"]
        assert collapsed == ["word_agent"]  # 别名行不再独立成条目，DB（后加载）胜出
        assert manager.get_agent("WordAgent")["timeout"] == 77
        assert manager.get_agent("word_agent")["timeout"] == 77

    def test_get_model_for_agent_resolves_alias(self, fresh_manager):
        manager = fresh_manager
        via_alias = manager.get_model_for_agent("word_agent")
        via_canonical = manager.get_model_for_agent("WordAgent")
        assert via_alias is not None
        assert via_alias["model_id"] == via_canonical["model_id"]

    def test_get_model_for_agent_missing_falls_back_to_default(self, fresh_manager):
        """兼容契约：未知 Agent 回退全局默认模型（既有行为不变）。"""
        assert fresh_manager.get_model_for_agent("missing") is not None

    def test_get_agent_prompt_via_alias(self, fresh_manager):
        manager = fresh_manager
        assert manager.get_agent_prompt("word_agent") == \
            manager.get_agent_prompt("WordAgent")
        assert manager.get_agent_prompt("Unknown") == ""
