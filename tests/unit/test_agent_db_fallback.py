"""agent.py 数据库故障降级的日志可见性测试。

根因背景：_get_db_session 曾 except Exception: return None，数据库
连接故障被静默吞掉，降级到内置清单时监控完全不可见（清单 488）。
"""
import asyncio
import logging

from office_agent.api.router import agent as agent_router


def test_session_failure_is_logged_and_falls_back(monkeypatch, caplog):
    """会话创建失败：必须留下结构化日志，且仍降级返回内置清单。"""
    import office_agent.database.session as db_session_module

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(db_session_module, "SessionLocal", boom)

    with caplog.at_level(logging.ERROR, logger="office_agent.api.agent"):
        result = asyncio.run(agent_router.list_agents())

    assert result.data.total == len(agent_router.BUILTIN_AGENTS)
    assert any("降级到内置清单" in record.message
               for record in caplog.records)


def test_query_failure_is_logged_and_falls_back(monkeypatch, caplog):
    """查询阶段失败：已有 logger.exception，回归锁定该行为。"""
    import office_agent.database.session as db_session_module
    from office_agent.database.repository import AgentRepository

    class _FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(db_session_module, "SessionLocal",
                        lambda: _FakeSession())

    def boom(_self):
        raise RuntimeError("query exploded")

    monkeypatch.setattr(AgentRepository, "get_enabled", boom)

    with caplog.at_level(logging.ERROR, logger="office_agent.api.agent"):
        result = asyncio.run(agent_router.list_agents())

    assert result.data.total == len(agent_router.BUILTIN_AGENTS)
    assert any("降级到内置清单" in record.message
               for record in caplog.records)
