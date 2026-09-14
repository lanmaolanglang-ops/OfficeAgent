import asyncio
import importlib
import subprocess


class RecordingAudit:
    def __init__(self):
        self.blocked_sandbox = []
        self.sandbox_timeouts = []
        self.blocked_tools = []
        self.tool_calls = []

    def log_sandbox_blocked(self, user_id, reason):
        self.blocked_sandbox.append((user_id, reason))

    def log_sandbox_timeout(self, user_id, timeout_seconds):
        self.sandbox_timeouts.append((user_id, timeout_seconds))

    def log_tool_blocked(self, user_id, agent, tool, reason):
        self.blocked_tools.append((user_id, agent, tool, reason))

    def log_tool_call(self, user_id, agent, tool, risk_level,
                      approval_granted):
        self.tool_calls.append(
            (user_id, agent, tool, risk_level, approval_granted)
        )


def test_agent_list_get_is_read_only_and_preserves_explicit_empty_config(monkeypatch):
    agent_router = importlib.import_module("office_agent.api.router.agent")
    repository = importlib.import_module("office_agent.database.repository")

    class Session:
        closed = False

        def commit(self):
            raise AssertionError("GET /agents must not commit")

        def close(self):
            self.closed = True

    class EmptyRepository:
        def __init__(self, _session):
            pass

        def get_enabled(self):
            return []

    session = Session()
    monkeypatch.setattr(agent_router, "_get_db_session", lambda: session)
    monkeypatch.setattr(repository, "AgentRepository", EmptyRepository)

    response = asyncio.run(agent_router.list_agents())

    assert response.data.total == 0
    assert response.data.agents == []
    assert session.closed


def test_sandbox_block_and_timeout_are_audited(monkeypatch, tmp_path):
    import office_agent.security.sandbox.sandbox as sandbox_module
    from office_agent.security.sandbox import Sandbox, SandboxStatus

    audit = RecordingAudit()
    sandbox = Sandbox(
        work_dir=tmp_path,
        timeout_seconds=3,
        allow_unsafe_subprocess=True,
        audit_logger=audit,
    )

    blocked = sandbox.execute("import os", user_id="user-1")
    assert blocked.status == SandboxStatus.BLOCKED
    assert audit.blocked_sandbox == [("user-1", blocked.error)]

    class TimeoutProcess:
        pid = 4321
        returncode = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired("python", timeout)

        def poll(self):
            return None

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            self.returncode = -9
            return self.returncode

    monkeypatch.setattr(
        sandbox_module.subprocess, "Popen",
        lambda *_args, **_kwargs: TimeoutProcess(),
    )
    monkeypatch.setattr(Sandbox, "_terminate_process_tree", lambda *_args: None)
    timed_out = sandbox.execute("result = 1", user_id="user-1")
    assert timed_out.status == SandboxStatus.TIMEOUT
    assert audit.sandbox_timeouts == [("user-1", 3)]


def test_high_risk_tool_requires_approval_and_audits_both_decisions():
    from office_agent.security.permission.agent_permissions import (
        AgentPermissionManager,
        RiskLevel,
        ToolInfo,
        ToolRegistry,
    )

    tool = ToolInfo(
        "dangerous", "dangerous", RiskLevel.HIGH,
        allowed_agents={"excel"}, requires_approval=True,
    )
    audit = RecordingAudit()
    manager = AgentPermissionManager(
        ToolRegistry({"dangerous": tool}), audit_logger=audit
    )

    denied = manager.can_use_tool("excel", "dangerous", user_id="user-1")
    # 调用方布尔不再放行；需 server-side approval_id
    bool_only = manager.can_use_tool(
        "excel", "dangerous", user_id="user-1", approval_granted=True
    )
    from office_agent.security.permission.approval_store import get_approval_store
    aid = get_approval_store().grant(
        user_id="user-1", agent="excel", tool_name="dangerous",
    )
    allowed = manager.can_use_tool(
        "excel", "dangerous", user_id="user-1", approval_id=aid,
    )

    assert not denied[0]
    assert "审批" in denied[1]
    assert bool_only[0] is False
    assert allowed == (True, "允许")
    assert audit.blocked_tools[0][:3] == ("user-1", "excel", "dangerous")
    assert audit.tool_calls == [
        ("user-1", "excel", "dangerous", "high", True)
    ]


def test_audit_failure_does_not_change_tool_security_decision():
    from office_agent.security.permission.agent_permissions import (
        AgentPermissionManager,
        RiskLevel,
        ToolInfo,
        ToolRegistry,
    )

    class BrokenAudit:
        def log_tool_call(self, *_args, **_kwargs):
            raise RuntimeError("audit unavailable")

    tool = ToolInfo(
        "high", "high", RiskLevel.HIGH, allowed_agents={"excel"}
    )
    manager = AgentPermissionManager(
        ToolRegistry({"high": tool}), audit_logger=BrokenAudit()
    )

    assert manager.can_use_tool("excel", "high") == (True, "允许")
