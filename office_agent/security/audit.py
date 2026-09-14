"""
Audit Logger - 安全审计日志
记录登录、权限拒绝、文件访问、危险操作等
"""
from __future__ import annotations

import queue
import threading
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
    """有界内存镜像 + 数据库持久化的安全审计日志。

    持久化语义（安全审计事件不允许静默丢失）：
    - ``log()`` 只做内存镜像更新与入队（临界区极小），写库由单个后台
      写线程批量完成——请求路径不再逐事件同步 commit；
    - 队列满（数据库长时间落后）时退化为调用线程**同步**持久化——
      宁可变慢，不丢事件；
    - 批量落库失败先整批重试一次，再逐条隔离重试，单条坏数据不拖垮
      整批；最终失败只记录错误日志（事件仍保留在有界内存镜像中可查）；
    - ``flush()`` 等待队列排空，``close()`` 结束写线程（先排空），
      供应用关闭与测试收尾调用。
    """

    def __init__(self, enable: bool = True, session_factory=None,
                 max_memory_entries: int = 1000, batch_size: int = 50,
                 batch_window: float = 0.5, queue_maxsize: int = 10000):
        self.enable = enable
        self._entries: list[AuditEntry] = []
        self._entries_lock = threading.Lock()
        self._callbacks: list[Callable[[AuditEntry], None]] = []
        self._session_factory = session_factory
        self._max_memory_entries = max(1, max_memory_entries)
        self._batch_size = max(1, int(batch_size))
        self._batch_window = max(0.0, float(batch_window))
        self._legacy_fk: bool | None = None  # 逐事件 FK 内省的缓存
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, queue_maxsize))
        self._closed = False
        self._writer = threading.Thread(
            target=self._writer_loop, daemon=True, name="audit-writer")
        self._writer.start()

    def _get_session_factory(self):
        if self._session_factory is None:
            from office_agent.database.session import SessionLocal
            self._session_factory = SessionLocal
        return self._session_factory

    def _resolve_legacy_fk(self, session) -> bool:
        """缓存 security_audit_logs 是否仍挂在旧 security_users 外键上。

        旧实现在每个事件上做一次表内省（get_foreign_keys），纯开销；
        运行期 schema 不会变化，按实例缓存一次即可。
        """
        if self._legacy_fk is None:
            from sqlalchemy import inspect
            inspector = inspect(session.connection())
            self._legacy_fk = any(
                fk.get("referred_table") == "security_users"
                for fk in inspector.get_foreign_keys("security_audit_logs")
            )
        return self._legacy_fk

    def _persist_entries(self, entries: list[AuditEntry]) -> None:
        """一个会话、一次 commit 批量持久化多条审计事件。"""
        from sqlalchemy import text
        from office_agent.database.models import AuditLogModel, User

        with self._get_session_factory()() as session:
            legacy_fk = self._resolve_legacy_fk(session)
            for entry in entries:
                details = dict(entry.details)
                occurred_at = datetime.fromtimestamp(
                    entry.timestamp, timezone.utc)
                persisted_user_id = entry.user_id
                if persisted_user_id:
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

    def _persist_batch(self, entries: list[AuditEntry]) -> None:
        try:
            self._persist_entries(entries)
        except Exception:
            logger.warning("审计批量持久化失败，降级为逐条重试", exc_info=True)
            for entry in entries:
                try:
                    self._persist_entries([entry])
                except Exception:
                    # 事件仍在有界内存镜像中（get_entries 可查），
                    # 这里只记录失败，不再无限重试。
                    logger.exception("Audit persistence failed")

    def _writer_loop(self) -> None:
        closed = False
        while not closed:
            entry = self._queue.get()
            if entry is None:
                closed = True
                self._queue.task_done()
                batch: list[AuditEntry] = []
            else:
                batch = [entry]
                deadline = time.monotonic() + self._batch_window
                while len(batch) < self._batch_size:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        next_entry = self._queue.get(timeout=remaining)
                    except queue.Empty:
                        break
                    if next_entry is None:
                        closed = True
                        break
                    batch.append(next_entry)
            if batch:
                try:
                    self._persist_batch(batch)
                finally:
                    for _ in batch:
                        self._queue.task_done()
            if closed:
                self._drain_and_finish()

    def _drain_and_finish(self) -> None:
        """关闭前排空队列中剩余事件。"""
        rest: list[AuditEntry] = []
        while True:
            try:
                entry = self._queue.get_nowait()
            except queue.Empty:
                break
            if entry is not None:
                rest.append(entry)
            self._queue.task_done()
        for index in range(0, len(rest), self._batch_size):
            self._persist_batch(rest[index:index + self._batch_size])

    def _enqueue(self, entry: AuditEntry) -> None:
        if self._closed:
            try:
                self._persist_entries([entry])
            except Exception:
                logger.exception("Audit persistence failed")
            return
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            # 队列满（数据库长时间落后）：调用线程同步持久化——
            # 宁可变慢，不丢事件。
            try:
                self._persist_entries([entry])
            except Exception:
                logger.exception("Audit persistence failed")

    def flush(self, timeout: float = 5.0) -> bool:
        """等待队列中所有事件落库（应用关闭/测试收尾）。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self._queue.unfinished_tasks > 0:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    def close(self, timeout: float = 5.0) -> None:
        """排空并结束后台写线程；之后的 log() 走同步持久化兜底。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put(None, timeout=max(0.0, float(timeout)))
        except queue.Full:
            logger.warning("审计队列已满，关闭信号未入队")
        self._writer.join(timeout=max(0.0, float(timeout)))

    def add_callback(self, callback: Callable[[AuditEntry], None]):
        """添加审计回调（用于写入数据库等）"""
        with self._entries_lock:
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

        # danger/critical 或 blocked/denied 属安全事件，即便审计被关闭，
        # 也必须至少打到日志，不能被开关一并静默（P3-58）。
        security_critical = (
            entry.risk_level in ("danger", "critical")
            or entry.status in ("blocked", "denied")
        )

        if self.enable:
            with self._entries_lock:
                self._entries.append(entry)
                overflow = len(self._entries) - self._max_memory_entries
                if overflow > 0:
                    del self._entries[:overflow]

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

            # 异步入队（后台写线程批量持久化）；队列满/已关闭时同步兜底
            self._enqueue(entry)

            # 回调
            with self._entries_lock:
                callbacks = list(self._callbacks)
            for cb in callbacks:
                try:
                    cb(entry)
                except Exception as e:
                    logger.error(f"Audit callback error: {e}")
        elif security_critical:
            logger.warning(
                "AUDIT(off) [%s] %s: user=%s resource=%s status=%s",
                entry.risk_level.upper(), entry.action,
                entry.user_id, entry.resource, entry.status,
            )

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
        with self._entries_lock:
            entries = list(self._entries)
        if user_id:
            entries = [e for e in entries if e.user_id == user_id]
        if action:
            entries = [e for e in entries if e.action == action]
        return entries[-limit:]

    def get_recent_dangerous(self, limit: int = 20) -> list[AuditEntry]:
        """获取最近的危险操作"""
        with self._entries_lock:
            entries = list(self._entries)
        dangerous = [e for e in entries
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
        with self._entries_lock:
            self._entries.clear()


# 全局审计日志
_audit_logger: AuditLogger | None = None
_audit_logger_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志（双检锁，避免并发重复构造，P3-57）"""
    global _audit_logger
    if _audit_logger is None:
        with _audit_logger_lock:
            if _audit_logger is None:
                _audit_logger = AuditLogger()
    return _audit_logger
