"""
Access Control - 访问控制
检查用户对资源的访问权限
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

from .roles import (
    Role, has_permission,
)

try:
    from office_agent.logging_system import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class AccessDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class AccessContext:
    """访问上下文"""
    user_id: str
    role: str
    resource: str  # 资源类型，如 file/task/agent
    action: str
    resource_id: str | None = None  # 具体资源ID
    resource_owner_id: str | None = None  # 资源所有者
    ip_address: str | None = None
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class AccessResult:
    """访问决策结果"""
    decision: AccessDecision
    reason: str = ""
    context: AccessContext | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == AccessDecision.ALLOW

    @property
    def denied(self) -> bool:
        return self.decision == AccessDecision.DENY


class AccessController:
    """
    访问控制器
    支持：
    1. RBAC权限检查
    2. 资源所有权检查（用户只能访问自己的资源）
    3. 自定义策略
    4. 访问审计回调
    """

    def __init__(self):
        self._custom_policies: list[Callable[[AccessContext], AccessResult | None]] = []
        self._audit_callback: Optional[Callable[[AccessContext, AccessResult], None]] = None
        # 资源所有者映射（生产环境用数据库）
        self._resource_owners: dict[str, str] = {}  # resource_id -> owner_id

    def register_resource_owner(self, resource_id: str, owner_id: str):
        """注册资源所有者"""
        self._resource_owners[resource_id] = owner_id

    def get_resource_owner(self, resource_id: str) -> str | None:
        """获取资源所有者"""
        return self._resource_owners.get(resource_id)

    def add_policy(self, policy: Callable[[AccessContext], AccessResult | None]):
        """添加自定义策略（返回None表示不决定，继续下一个策略）"""
        self._custom_policies.append(policy)

    def set_audit_callback(self, callback: Callable[[AccessContext, AccessResult], None]):
        """设置审计回调"""
        self._audit_callback = callback

    def check(self, ctx: AccessContext) -> AccessResult:
        """
        检查访问权限
        决策链：自定义策略 → RBAC权限 → 所有权检查
        """
        # 1. 自定义策略优先
        for policy in self._custom_policies:
            try:
                result = policy(ctx)
                if result is not None:
                    self._audit(ctx, result)
                    return result
            except Exception as e:
                result = AccessResult(
                    decision=AccessDecision.DENY,
                    reason=f"访问策略执行失败: {type(e).__name__}",
                    context=ctx,
                )
                logger.error("Policy failed closed", exc_info=True)
                self._audit(ctx, result)
                return result

        # 2. RBAC权限检查
        # resource可能是资源类型(file)或具体资源ID(file_001)
        resource_type = ctx.resource
        resource_id = ctx.resource_id
        # 如果resource在已注册的所有者中，说明它是资源ID，需要查所有者
        if not resource_id and ctx.resource in self._resource_owners:
            resource_id = ctx.resource
        # 如果有resource_id，查注册的所有者
        owner_id = ctx.resource_owner_id
        if resource_id and not owner_id:
            owner_id = self._resource_owners.get(resource_id)

        perm_key = f"{resource_type}:{ctx.action}"
        if not has_permission(ctx.role, perm_key):
            result = AccessResult(
                decision=AccessDecision.DENY,
                reason=f"角色 '{ctx.role}' 没有权限 '{perm_key}'",
                context=ctx,
            )
            self._audit(ctx, result)
            return result

        # 3. 所有权检查（非管理员只能访问自己的资源）。资源明确到 ID 时，
        # 未能解析 owner 不能等价于“公共资源”，否则漏注册即可绕过隔离。
        if resource_id and not owner_id and ctx.role != Role.ADMIN:
            result = AccessResult(
                decision=AccessDecision.DENY,
                reason=f"资源 '{resource_id}' 未注册所有者，拒绝访问",
                context=ctx,
            )
            self._audit(ctx, result)
            return result

        if owner_id and ctx.role != Role.ADMIN:
            if owner_id != ctx.user_id:
                result = AccessResult(
                    decision=AccessDecision.DENY,
                    reason=f"用户 '{ctx.user_id}' 不是资源 '{resource_id or resource_type}' 的所有者",
                    context=ctx,
                )
                self._audit(ctx, result)
                return result

        # 允许访问
        result = AccessResult(
            decision=AccessDecision.ALLOW,
            reason="权限检查通过",
            context=ctx,
        )
        self._audit(ctx, result)
        return result

    def can(self, user_id: str, role: str, resource: str, action: str,
            resource_owner_id: str | None = None) -> bool:
        """便捷方法：检查是否有权限"""
        ctx = AccessContext(
            user_id=user_id,
            role=role,
            resource=resource,
            action=action,
            resource_owner_id=resource_owner_id,
        )
        return self.check(ctx).allowed

    def require(self, user_id: str, role: str, resource: str, action: str,
                resource_owner_id: str | None = None) -> AccessResult:
        """要求权限，拒绝时抛出异常"""
        ctx = AccessContext(
            user_id=user_id,
            role=role,
            resource=resource,
            action=action,
            resource_owner_id=resource_owner_id,
        )
        result = self.check(ctx)
        if result.denied:
            raise PermissionError(result.reason)
        return result

    def _audit(self, ctx: AccessContext, result: AccessResult):
        """记录审计日志"""
        if result.denied:
            logger.warning(
                f"ACCESS DENIED: user={ctx.user_id} role={ctx.role} "
                f"resource={ctx.resource}:{ctx.action} reason={result.reason}"
            )
        else:
            logger.debug(
                f"ACCESS ALLOWED: user={ctx.user_id} role={ctx.role} "
                f"resource={ctx.resource}:{ctx.action}"
            )
        if self._audit_callback:
            try:
                self._audit_callback(ctx, result)
            except Exception as e:
                logger.warning(f"Audit callback error: {e}")


# 全局访问控制器实例
_default_controller: AccessController | None = None


def get_access_controller() -> AccessController:
    """获取全局访问控制器"""
    global _default_controller
    if _default_controller is None:
        _default_controller = AccessController()
    return _default_controller
