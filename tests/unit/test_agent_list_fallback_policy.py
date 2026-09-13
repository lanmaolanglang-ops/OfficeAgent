"""P5-15：Agent 降级清单语义回归（禁用过滤 + 显式 degraded）。

修复前：数据库不可用（会话创建失败或查询失败）时，``/api/agents`` 直接返回
硬编码的 ``BUILTIN_AGENTS``，全部标 ``status="available"`` 且不叠加"禁用"
过滤——被运维显式禁用/删除的内置 agent 仍会被重新列为可用，而调用方也无从
得知这不是用户配置的 agent 集合（静默降级）。

修复后：降级清单 ①尽力按数据库中的禁用状态过滤；②状态显式标为
``degraded``；③响应体带 ``degraded=True``；④日志记录
requested=configured_agents / effective=builtin_fallback。降级路径自身的
任何二次失败都退化为"不过滤"，绝不抛第二个错误。
"""
import asyncio
import logging

import pytest

from office_agent.api.core.exceptions import AgentNotFoundError
from office_agent.api.router import agent as agent_router
from office_agent.api.schemas.response import AgentListResponse


class _FakeSession:
    def close(self):
        pass


def _patch_session(monkeypatch, session):
    import office_agent.database.session as db_session_module
    monkeypatch.setattr(db_session_module, "SessionLocal", lambda: session)


def test_schema_degraded_flag_is_backward_compatible():
    """新增字段默认 False：既有调用方无需改动。"""
    assert AgentListResponse(agents=[], total=0).degraded is False
    assert AgentListResponse(agents=[], total=0).model_dump()["degraded"] is False


def test_session_failure_marks_degraded_and_logs_identity(monkeypatch, caplog):
    import office_agent.database.session as db_session_module

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(db_session_module, "SessionLocal", boom)

    with caplog.at_level(logging.WARNING, logger="office_agent.api.agent"):
        result = asyncio.run(agent_router.list_agents())

    assert result.data.degraded is True, "降级必须显式标记，不得静默"
    assert result.data.total == len(agent_router.BUILTIN_AGENTS)
    assert all(a.status == agent_router.DEGRADED_AGENT_STATUS
               for a in result.data.agents), "降级清单不得再声称 available"
    joined = " ".join(r.message for r in caplog.records)
    assert "requested=configured_agents" in joined
    assert "effective=builtin_fallback" in joined


def test_query_failure_applies_disabled_filter(monkeypatch, caplog):
    """会话可用但查询失败：必须按禁用状态过滤内置清单。"""
    from office_agent.database.repository import AgentRepository

    _patch_session(monkeypatch, _FakeSession())
    monkeypatch.setattr(AgentRepository, "get_enabled",
                        lambda _self: (_ for _ in ()).throw(RuntimeError("query exploded")))
    monkeypatch.setattr(AgentRepository, "get_disabled_agent_ids",
                        lambda _self: {"ppt_agent"})

    with caplog.at_level(logging.WARNING, logger="office_agent.api.agent"):
        result = asyncio.run(agent_router.list_agents())

    ids = {a.agent_id for a in result.data.agents}
    assert "ppt_agent" not in ids, "被禁用的内置 agent 不得重新出现在降级清单"
    assert result.data.total == len(agent_router.BUILTIN_AGENTS) - 1
    assert result.data.degraded is True
    assert all(a.status == agent_router.DEGRADED_AGENT_STATUS
               for a in result.data.agents)


def test_disabled_filter_failure_degrades_to_no_filter_without_second_error(monkeypatch, caplog):
    """禁用状态本身读不到时：退化为不过滤，降级路径不得再抛错。"""
    from office_agent.database.repository import AgentRepository

    _patch_session(monkeypatch, _FakeSession())
    monkeypatch.setattr(AgentRepository, "get_enabled",
                        lambda _self: (_ for _ in ()).throw(RuntimeError("query exploded")))
    monkeypatch.setattr(AgentRepository, "get_disabled_agent_ids",
                        lambda _self: (_ for _ in ()).throw(RuntimeError("disabled read exploded")))

    with caplog.at_level(logging.WARNING, logger="office_agent.api.agent"):
        result = asyncio.run(agent_router.list_agents())

    assert result.data.degraded is True
    assert result.data.total == len(agent_router.BUILTIN_AGENTS)
    assert any("无法读取禁用状态" in r.message for r in caplog.records)


def test_detail_fallback_is_marked_degraded(monkeypatch):
    from office_agent.database.repository import AgentRepository

    _patch_session(monkeypatch, _FakeSession())
    monkeypatch.setattr(AgentRepository, "get_by_agent_id",
                        lambda _self, _agent_id: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(AgentRepository, "get_disabled_agent_ids", lambda _self: set())

    fallback_id = agent_router.BUILTIN_AGENTS[0]["agent_id"]
    result = asyncio.run(agent_router.get_agent(fallback_id))
    assert result.data.agent_id == fallback_id
    assert result.data.status == agent_router.DEGRADED_AGENT_STATUS


def test_detail_disabled_builtin_is_not_found(monkeypatch):
    """详情降级同样尊重"已禁用"：禁用的内置 agent 视为不存在。"""
    from office_agent.database.repository import AgentRepository

    _patch_session(monkeypatch, _FakeSession())
    monkeypatch.setattr(AgentRepository, "get_by_agent_id",
                        lambda _self, _agent_id: (_ for _ in ()).throw(RuntimeError("boom")))
    disabled_id = agent_router.BUILTIN_AGENTS[0]["agent_id"]
    monkeypatch.setattr(AgentRepository, "get_disabled_agent_ids",
                        lambda _self: {disabled_id})

    with pytest.raises(AgentNotFoundError):
        asyncio.run(agent_router.get_agent(disabled_id))


def test_fallback_never_claims_available():
    """源码守卫：降级清单构造不得再直接返回 status="available" 的原样内置项。"""
    import inspect
    source = inspect.getsource(agent_router._builtin_fallback_agents)
    assert "DEGRADED_AGENT_STATUS" in source
    assert "disabled_ids" in source


def test_fallback_responses_declare_degraded():
    """源码守卫：两处降级返回都必须声明 degraded=True（可观察，非静默）。"""
    import inspect
    assert "degraded=True" in inspect.getsource(agent_router.list_agents)
