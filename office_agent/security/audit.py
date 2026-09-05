"""
Audit Logger - 安全审计日志
记录登录、权限拒绝、文件访问、危险操作等
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, cast

from sqlalchemy.engine import CursorResult

try:
    from office_agent.logging_system import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class AuditAction(str, Enum):
    # 认证
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    TOKEN_REFRESH = "token_refresh"
    # 权限
    ACCESS_GRANTED = "access_granted"
    ACCESS_DENIED = "access_denied"
    PERMISSION_DENIED = "permission_denied"
    # 文件
    FILE_UPLOAD = "file_upload"
    FILE_DOWNLOAD = "file_download"
    FILE_DELETE = "file_delete"
    FILE_BLOCKED = "file_blocked"
    # Agent
    AGENT_CALL = "agent_call"
    AGENT_BLOCKED = "agent_blocked"
    TOOL_CALL = "tool_call"
    TOOL_BLOCKED = "tool_blocked"
    # 模型
    MODEL_CALL = "model_call"
    PROMPT_INJECTION = "prompt_injection"
    # 沙箱
    SANDBOX_EXECUTE = "sandbox_execute"
    SANDBOX_BLOCKED = "sandbox_blocked"
    SANDBOX_TIMEOUT = "sandbox_timeout"
    # 任务生命周期（终态安全审计；进度/步骤细节由 ExecutionLog 承担）
    TASK_SUCCESS = "task_success"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    # 工作流
    WORKFLOW_CREATE = "workflow_create"
    WORKFLOW_CANCEL = "workflow_cancel"
    # 管理
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    CONFIG_CHANGE = "config_change"
    API_KEY_CREATE = "api_key_create"
    API_KEY_REVOKE = "api_key_revoke"


class RiskLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    DANGER = "danger"
    CRITICAL = "critical"


@dataclass
class AuditEntry:
    """审计条目"""
    action: str
    status: str  # success/denied/error
    user_id: str | None = None
    resource: str | None = None
    resource_id: str | None = None
    ip_address: str | None = None
    details: dict = field(default_factory=dict)
    risk_level: str = "info"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "status": self.status,
            "user_id": self.user_id,
            "resource": self.resource,
            "resource_id": self.resource_id,
            "ip_address": self.ip_address,
            "details": self.details,
            "risk_level": self.risk_level,
            "timestamp": self.timestamp,
        }


class AuditLogger:
    """有界内存镜像 + 数据库持久化的安全审计日志。"""

    def __init__(self, enable: bool = True, session_factory=None,
                 max_memory_entries: int = 1000):
        self.enable = enable
        self._entries: list[AuditEntry] = []
        self._callbacks: list[Callable[[AuditEntry], None]] = []
        self._session_factory = session_factory
        self._max_memory_entries = max(1, max_memory_entries)

    def _get_session_factory(self):
        if self._session_factory is None:
            from office_agent.database.session import SessionLocal
            self._session_factory = SessionLocal
        return self._session_factory

    def _persist(self, entry: AuditEntry) -> None:
        from sqlalchemy import inspect, text
        from office_agent.database.models import AuditLogModel, User

        details = dict(entry.details)
        occurred_at = datetime.fromtimestamp(entry.timestamp, timezone.utc)
        with self._get_session_factory()() as session:
            persisted_user_id = entry.user_id
            if persisted_user_id:
                inspector = inspect(session.connection())
                legacy_fk = any(
                    fk.get("referred_table") == "security_users"
                    for fk in inspector.get_foreign_keys("security_audit_logs")
                )
                if legacy_fk:
                    known = session.execute(text(
                        "SELECT 1 FROM security_users WHERE id=:id"
                    ), {"id": persisted_user_id}).first()
                else:
                    known = session.get(User, persisted_user_id)
                if known is None:
                    details.setdefault("subject_user_id", persisted_user_id)
                    persisted_user_id = None
            session.add(AuditLogModel(
                user_id=persisted_user_id,
                action=entry.action,
                resource=entry.resource,
                resource_id=entry.resource_id,
                status=entry.status,
                ip_address=entry.ip_address,
                details=details,
                risk_level=entry.risk_level,
                timestamp=occurred_at,
                created_at=occurred_at,
                updated_at=occurred_at,
            ))
            session.commit()

    def add_callback(self, callback: Callable[[AuditEntry], None]):
        """添加审计回调（用于写入数据库等）"""
        self._callbacks.append(callback)

    def log(self, action: str | AuditAction, status: str = "success",
            user_id: str | None = None, resource: str | None = None,
            resource_id: str | None = None, ip_address: str | None = None,
            details: dict | None = None, risk_level: str = "info") -> AuditEntry:
        """记录审计日志"""
        entry = AuditEntry(
            action=action if isinstance(action, str) else action.value,
            status=status,
            user_id=user_id,
            resource=resource,
            resource_id=resource_id,
            ip_address=ip_address,
            details=details or {},
            risk_level=risk_level,
        )

        if self.enable:
            self._entries.append(entry)
            if len(self._entries) > self._max_memory_entries:
                del self._entries[:-self._max_memory_entries]

            # 日志输出
            log_msg = (
                f"AUDIT [{entry.risk_level.upper()}] {entry.action}: "
                f"user={entry.user_id} resource={entry.resource} "
                f"status={entry.status}"
            )
            if entry.risk_level in ("danger", "critical"):
                logger.warning(log_msg)
            elif entry.risk_level == "warning":
                logger.info(log_msg)
            else:
                logger.debug(log_msg)

            try:
                self._persist(entry)
            except Exception:
                logger.exception("Audit persistence failed")

            # 回调
            for cb in self._callbacks:
                try:
                    cb(entry)
                except Exception as e:
                    logger.error(f"Audit callback error: {e}")

        return entry

    def log_login(self, user_id: str, success: bool, ip: str | None = None,
                  username: str | None = None):
        """记录登录"""
        if success:
            return self.log(
                AuditAction.LOGIN, "success",
                user_id=user_id, ip_address=ip,
                details={"username": username},
                risk_level="info",
            )
        else:
            return self.log(
                AuditAction.LOGIN_FAILED, "denied",
                user_id=user_id, ip_address=ip,
                details={"username": username},
                risk_level="warning",
            )

    def log_access_denied(self, user_id: str, resource: str, action: str,
                          reason: str = "", ip: str | None = None):
        """记录访问拒绝"""
        return self.log(
            AuditAction.ACCESS_DENIED, "denied",
            user_id=user_id, resource=resource,
            details={"action": action, "reason": reason},
            ip_address=ip, risk_level="warning",
        )

    def log_file_blocked(self, user_id: str, filename: str, reason: str,
                         ip: str | None = None):
        """记录文件阻止"""
        return self.log(
            AuditAction.FILE_BLOCKED, "denied",
            user_id=user_id, resource="file", resource_id=filename,
            details={"reason": reason}, ip_address=ip,
            risk_level="danger",
        )

    def log_prompt_injection(self, user_id: str, matches: list,
                             ip: str | None = None):
        """记录Prompt注入"""
        return self.log(
            AuditAction.PROMPT_INJECTION, "blocked",
            user_id=user_id, resource="prompt",
            details={"matches": [m.description for m in matches]},
            ip_address=ip, risk_level="critical",
        )

    def log_sandbox_blocked(self, user_id: str | None, reason: str):
        """记录沙箱阻止"""
        return self.log(
            AuditAction.SANDBOX_BLOCKED, "blocked",
            user_id=user_id, resource="sandbox",
            details={"reason": reason},
            risk_level="danger",
        )

    def log_sandbox_timeout(self, user_id: str | None, timeout_seconds: int):
        """记录沙箱超时。"""
        return self.log(
            AuditAction.SANDBOX_TIMEOUT, "timeout",
            user_id=user_id, resource="sandbox",
            details={"timeout_seconds": timeout_seconds},
            risk_level="danger",
        )

    def log_tool_blocked(self, user_id: str | None, agent: str, tool: str,
                         reason: str):
        """记录工具阻止"""
        return self.log(
            AuditAction.TOOL_BLOCKED, "denied",
            user_id=user_id, resource=f"tool:{tool}",
            details={"agent": agent, "reason": reason},
            risk_level="warning",
        )

    def log_tool_call(self, user_id: str | None, agent: str, tool: str,
                      risk_level: str, approval_granted: bool):
        """记录已授权的高风险工具调用。"""
        return self.log(
            AuditAction.TOOL_CALL, "success",
            user_id=user_id, resource=f"tool:{tool}",
            details={
                "agent": agent,
                "approval_granted": approval_granted,
            },
            risk_level=risk_level,
        )

    def log_task_transition(self, task_id: str, status: str,
                            user_id: str | None = None,
                            task_type: str | None = None,
                            agent: str | None = None,
                            duration_ms: int | None = None,
                            error: str | None = None):
        """记录任务终态（success/failed/cancelled）安全审计事件。

        只记录安全与可追踪元数据；任务进度、步骤与输入输出摘要仍由
        ExecutionLog / log_task_event 承担，不在此重复。
        """
        actions = {
            "success": AuditAction.TASK_SUCCESS,
            "failed": AuditAction.TASK_FAILED,
            "cancelled": AuditAction.TASK_CANCELLED,
        }
        action = actions.get(status)
        if action is None:
            raise ValueError(f"未知任务终态: {status}")
        details = {
            "task_type": task_type,
            "agent": agent,
            "duration_ms": duration_ms,
        }
        if error:
            details["error"] = str(error)[:500]
        return self.log(
            action, status,
            user_id=user_id, resource="task", resource_id=task_id,
            details={key: value for key, value in details.items()
                     if value is not None},
            risk_level="warning" if status == "failed" else "info",
        )

    def log_model_call(self, status: str, model_id: str | None = None,
                       provider: str | None = None,
                       user_id: str | None = None,
                       details: dict | None = None):
        """记录一次模型调用的安全审计事件。

        只允许安全元数据（provider、canonical model ID、关联 ID、
        归一化失败类别、耗时等）；严禁传入 prompt、响应正文、API Key
        或解密后的模型密钥。
        """
        safe_details = dict(details or {})
        for forbidden in ("prompt", "messages", "response", "content",
                          "api_key", "secret", "authorization"):
            safe_details.pop(forbidden, None)
        return self.log(
            AuditAction.MODEL_CALL, status,
            user_id=user_id,
            resource=f"model:{model_id or 'unknown'}",
            details=({"provider": provider} if provider else {}) | safe_details,
            risk_level="info" if status == "success" else "warning",
        )

    def get_entries(self, user_id: str | None = None,
                    action: str | None = None,
                    limit: int = 100) -> list[AuditEntry]:
        """查询审计条目"""
        entries = self._entries
        if user_id:
            entries = [e for e in entries if e.user_id == user_id]
        if action:
            entries = [e for e in entries if e.action == action]
        return entries[-limit:]

    def get_recent_dangerous(self, limit: int = 20) -> list[AuditEntry]:
        """获取最近的危险操作"""
        dangerous = [e for e in self._entries
                    if e.risk_level in ("danger", "critical")]
        return dangerous[-limit:]

    def cleanup_expired(self, retention_days: int = 90) -> int:
        """按保留策略清理持久化审计记录。"""
        if retention_days <= 0:
            raise ValueError("retention_days 必须大于 0")
        from sqlalchemy import delete
        from office_agent.database.models import AuditLogModel

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with self._get_session_factory()() as session:
            result = cast("CursorResult[Any]", session.execute(
                delete(AuditLogModel).where(AuditLogModel.timestamp < cutoff)
            ))
            session.commit()
            return int(result.rowcount or 0)

    def clear(self):
        """清空（测试用）"""
        self._entries.clear()


# 全局审计日志
_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger
