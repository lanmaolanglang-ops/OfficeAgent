"""Server-side tool approval store (P2-59).

调用方布尔 ``approval_granted=True`` 不再是信任根：高风险工具必须持有
本 store 签发的一次性/限时 approval id，且与 user/agent/tool 绑定。
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field


@dataclass
class ApprovalRecord:
    approval_id: str
    user_id: str
    agent: str
    tool_name: str
    expires_at: float
    consumed: bool = False
    created_at: float = field(default_factory=time.time)


class ApprovalStore:
    """进程内审批存储（本地桌面单后端定位）。"""

    def __init__(self, default_ttl_seconds: float = 300.0, max_records: int = 1000):
        self._lock = threading.Lock()
        self._records: dict[str, ApprovalRecord] = {}
        self._default_ttl = default_ttl_seconds
        self._max_records = max_records

    def grant(self, *, user_id: str, agent: str, tool_name: str,
              ttl_seconds: float | None = None) -> str:
        # 审批必须绑定到具体 user/agent/tool；签发未绑定凭证等于没有信任根。
        if not user_id or not agent or not tool_name:
            raise ValueError("审批签发必须提供非空 user_id/agent/tool_name")
        approval_id = secrets.token_urlsafe(24)
        rec = ApprovalRecord(
            approval_id=approval_id,
            user_id=user_id or "",
            agent=agent or "",
            tool_name=tool_name or "",
            expires_at=time.time() + (ttl_seconds or self._default_ttl),
        )
        with self._lock:
            self._prune_unlocked()
            if len(self._records) >= self._max_records:
                raise RuntimeError("审批存储已满")
            self._records[approval_id] = rec
        return approval_id

    def consume(self, approval_id: str, *, user_id: str, agent: str,
                tool_name: str) -> bool:
        """验证并消费一次性审批；任一绑定不匹配或过期/已消费均拒绝。"""
        if not approval_id:
            return False
        with self._lock:
            rec = self._records.get(approval_id)
            if rec is None or rec.consumed:
                return False
            if rec.expires_at < time.time():
                del self._records[approval_id]
                return False
            # 三个绑定都必须非空且全等：任一为空即无法证明归属，fail closed，
            # 不允许“调用方留空 user_id”来绕过用户/工具绑定。
            if not user_id or not agent or not tool_name:
                return False
            if (rec.user_id, rec.agent, rec.tool_name) != (user_id, agent, tool_name):
                return False
            rec.consumed = True
            return True

    def _prune_unlocked(self) -> None:
        now = time.time()
        expired = [k for k, r in self._records.items() if r.expires_at < now]
        for k in expired:
            del self._records[k]


_approval_store: ApprovalStore | None = None
_approval_lock = threading.Lock()


def get_approval_store() -> ApprovalStore:
    global _approval_store
    if _approval_store is None:
        with _approval_lock:
            if _approval_store is None:
                _approval_store = ApprovalStore()
    return _approval_store
