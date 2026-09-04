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


def test_detail_query_failure_is_logged_and_falls_back(monkeypatch, caplog):
    """详情查询失败（agent.py:123 降级腿）：必须留日志且降级到内置清单。"""
    import office_agent.database.session as db_session_module
    from office_agent.database.repository import AgentRepository

    class _FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(db_session_module, "SessionLocal",
                        lambda: _FakeSession())

    def boom(_self, _agent_id):
        raise RuntimeError("detail query exploded")

    monkeypatch.setattr(AgentRepository, "get_by_agent_id", boom)

    fallback_id = agent_router.BUILTIN_AGENTS[0]["agent_id"]
    with caplog.at_level(logging.ERROR, logger="office_agent.api.agent"):
        result = asyncio.run(agent_router.get_agent(fallback_id))

    assert result.data.agent_id == fallback_id
    assert any("读取 Agent 详情失败" in record.message
               for record in caplog.records)


def test_no_silent_except_in_agent_router():
    """源码守卫：agent.py 的每个 except Exception 都必须带日志，
    不得再出现静默吞错（清单 549 防回退）。"""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(agent_router))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # 只约束裸 Exception 吞错腿；精确异常（如 JSONDecodeError）除外
        if not (isinstance(node.type, ast.Name) and node.type.id == "Exception"):
            continue
        body_src = ast.dump(node.body[0]) if node.body else ""
        assert "logger" in body_src or "raise" in body_src, (
            f"agent.py:{node.lineno} 存在不带日志的 except Exception 吞错腿"
        )
