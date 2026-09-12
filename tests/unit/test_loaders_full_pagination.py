"""P2-6：loaders 全量加载分页化 + GlobalConfig.debug fail-safe 默认回归。

修复前：DatabaseLoader 五个全量加载走固定 ``find(limit=1000/100)``——
超出上限的配置行被**静默截断**（第 1001 个模型在配置缓存里永远不可
见，且无任何告警），而这里的语义是"加载全部记录"，不是产品分页；
``GlobalConfig.debug`` 默认 ``True``（生产 fail-safe 应默认关闭）。
"""
import pytest

import office_agent.database.repository as repository_module
from office_agent.config_system.loaders import LOAD_PAGE_SIZE, DatabaseLoader, EnvLoader
from office_agent.config_system.schemas import GlobalConfig
from office_agent.config_system.validators import ConfigValidator
from types import SimpleNamespace


class _Session:
    def __init__(self, closed):
        self._closed = closed

    def close(self):
        self._closed.append(True)


def _make_repo(key: str, rows: list, fail_on_offset: int | None = None):
    """模拟真实 find(offset, limit, order_by) 的分页语义的仓库桩。"""

    class _Repo:
        def __init__(self, session):
            pass

        def find(self, offset=0, limit=100, order_by=None, descending=False,
                 **filters):
            assert 1 <= limit <= 1000, "必须走有界分页"
            assert order_by == key, "全量分页必须按唯一业务键稳定排序"
            if fail_on_offset is not None and offset == fail_on_offset:
                raise RuntimeError("db page failure")
            ordered = sorted(rows, key=lambda item: getattr(item, key))
            return ordered[offset:offset + limit]

        def get_enabled(self):
            return [item for item in rows if getattr(item, "enabled", True)]

        def to_dict(self, item):
            return {key: getattr(item, key)}

    return _Repo


def _run_loader(monkeypatch, repo_cls, repo_name, method, args=()):
    closed = []
    monkeypatch.setattr(repository_module, repo_name, repo_cls)
    loader = DatabaseLoader(lambda: _Session(closed))
    result = getattr(loader, method)(*args)
    assert closed == [True], "会话必须在加载后关闭"
    return result


class TestFullLoadPagination:
    @pytest.mark.parametrize("repo_name,method,key,count", [
        ("ModelConfigRepository", "load_models", "model_id", 1250),
        ("AgentConfigRepository", "load_agents", "agent_name", 1500),
        ("PromptConfigRepository", "load_prompts", "name", 1000),
        ("SkillConfigRepository", "load_skills", "skill_name", 1001),
        ("WorkflowConfigRepository", "load_workflows", "workflow_name", 1200),
    ])
    def test_beyond_fixed_limit_loads_completely(
            self, monkeypatch, repo_name, method, key, count):
        """超过旧固定上限（1000/100）的记录必须完整加载，不重不漏。"""
        rows = [SimpleNamespace(**{key: f"{key}-{i:05d}"})
                for i in range(count)]
        repo_cls = _make_repo(key, rows)
        result = _run_loader(monkeypatch, repo_cls, repo_name, method,
                             (False,))
        assert len(result) == count
        keys = [item[key] for item in result]
        assert keys == sorted(keys), "顺序必须按唯一键稳定保持"
        assert len(set(keys)) == count, "分页不得产生重复"

    def test_empty_repository(self, monkeypatch):
        repo_cls = _make_repo("model_id", [])
        result = _run_loader(monkeypatch, repo_cls, "ModelConfigRepository",
                             "load_models", (False,))
        assert result == []

    def test_single_short_page(self, monkeypatch):
        rows = [SimpleNamespace(model_id=f"m-{i}") for i in range(3)]
        repo_cls = _make_repo("model_id", rows)
        result = _run_loader(monkeypatch, repo_cls, "ModelConfigRepository",
                             "load_models", (False,))
        assert len(result) == 3

    def test_page_size_is_bounded(self, monkeypatch):
        """单页拉取不得无界：页大小必须等于 LOAD_PAGE_SIZE。"""
        seen_limits = []

        class _LimitSpyRepo(_make_repo("model_id", [
                SimpleNamespace(model_id=f"m-{i}") for i in range(10)])):
            def find(self, offset=0, limit=100, order_by=None,
                     descending=False, **filters):
                seen_limits.append(limit)
                return super().find(offset=offset, limit=limit,
                                    order_by=order_by,
                                    descending=descending, **filters)

        _run_loader(monkeypatch, _LimitSpyRepo, "ModelConfigRepository",
                    "load_models", (False,))
        assert seen_limits == [LOAD_PAGE_SIZE]

    def test_page_failure_propagates_and_session_closes(self, monkeypatch):
        """某页查询失败必须向上传播（不得静默当空页），会话仍被关闭。"""
        rows = [SimpleNamespace(model_id=f"m-{i:05d}")
                for i in range(LOAD_PAGE_SIZE + 5)]
        repo_cls = _make_repo("model_id", rows, fail_on_offset=LOAD_PAGE_SIZE)
        with pytest.raises(RuntimeError):
            _run_loader(monkeypatch, repo_cls, "ModelConfigRepository",
                        "load_models", (False,))


class TestDebugFailSafeDefault:
    def test_default_is_off(self):
        assert GlobalConfig().debug is False

    def test_explicit_true_still_honored(self):
        assert GlobalConfig(debug=True).debug is True

    def test_env_override_still_honored(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "true")
        assert EnvLoader.load().get("debug") is True
        monkeypatch.setenv("DEBUG", "false")
        assert EnvLoader.load().get("debug") is False

    def test_production_default_does_not_warn_about_debug(self):
        issues = ConfigValidator().validate_global(
            GlobalConfig(environment="production"))
        assert not any("debug" in issue.message.lower() for issue in issues)

    def test_production_explicit_debug_still_warns(self):
        issues = ConfigValidator().validate_global(
            GlobalConfig(environment="production", debug=True))
        assert any("debug" in issue.message.lower() for issue in issues)
