"""Database-backed RBAC resolution and idempotent system-role seeding."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable

from sqlalchemy import select

from .roles import PERMISSIONS, ROLE_INFO, ROLE_PERMISSIONS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    role: str = ""
    reason: str = ""


def seed_default_rbac(session_factory: Callable | None = None) -> None:
    """Create missing system permissions and roles without overwriting DB policy."""
    if session_factory is None:
        from ...database.session import SessionLocal
        session_factory = SessionLocal
    from ...database.models import PermissionModel, RoleModel

    session = session_factory()
    try:
        existing_permissions = {
            row.name for row in session.scalars(select(PermissionModel)).all()
        }
        for name, permission in PERMISSIONS.items():
            if name not in existing_permissions:
                session.add(PermissionModel(
                    id=f"perm_system_{name.replace(':', '_')}",
                    name=name,
                    resource=permission.resource,
                    action=permission.action,
                    description=permission.description,
                    risk_level="high" if permission.resource == "admin" else "low",
                ))

        existing_roles = {row.name for row in session.scalars(select(RoleModel)).all()}
        for role_name, info in ROLE_INFO.items():
            name = role_name.value
            if name not in existing_roles:
                session.add(RoleModel(
                    id=f"role_system_{name}",
                    name=name,
                    display_name=info.display_name,
                    description=info.description,
                    permissions=sorted(ROLE_PERMISSIONS[role_name]),
                    is_system=True,
                    is_active=True,
                ))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class DatabasePermissionResolver:
    """Resolve the current user's role and permissions from the database."""

    def __init__(self, session_factory: Callable | None = None):
        if session_factory is None:
            from ...database.session import SessionLocal
            session_factory = SessionLocal
        self._session_factory = session_factory

    def check(self, user_id: str, required: str | Iterable[str]) -> PermissionDecision:
        required_permissions = {required} if isinstance(required, str) else set(required)
        if not user_id or not required_permissions:
            return PermissionDecision(False, reason="用户或权限要求为空")

        from ...database.models import PermissionModel, RoleModel, User

        session = None
        try:
            session = self._session_factory()
            user = session.get(User, user_id)
            if user is None or not user.is_active:
                return PermissionDecision(False, reason="用户不存在或已停用")
            role = session.scalar(select(RoleModel).where(
                RoleModel.name == user.role,
                RoleModel.is_active.is_(True),
            ))
            if role is None or not isinstance(role.permissions, list):
                return PermissionDecision(False, role=user.role, reason="角色不存在或已停用")

            assigned = {item for item in role.permissions if isinstance(item, str)}
            known = set(session.scalars(select(PermissionModel.name).where(
                PermissionModel.name.in_(required_permissions)
            )).all())
            missing = required_permissions - assigned
            unregistered = required_permissions - known
            if missing or unregistered:
                reason = "缺少权限: " + ", ".join(sorted(missing | unregistered))
                return PermissionDecision(False, role=role.name, reason=reason)
            return PermissionDecision(True, role=role.name, reason="数据库权限检查通过")
        except Exception as exc:
            logger.error("Database RBAC check failed closed", exc_info=True)
            return PermissionDecision(False, reason=f"权限服务不可用: {type(exc).__name__}")
        finally:
            if session is not None:
                session.close()

    def check_ownership(self, user_id: str, resource: str,
                        resource_id: str) -> PermissionDecision:
        """Require object ownership for non-admin users; failures deny access."""
        from ...database.models import File, RoleModel, Task, User

        session = None
        try:
            session = self._session_factory()
            user = session.get(User, user_id)
            if user is None or not user.is_active:
                return PermissionDecision(False, reason="用户不存在或已停用")
            role = session.scalar(select(RoleModel).where(
                RoleModel.name == user.role,
                RoleModel.is_active.is_(True),
            ))
            if role is None:
                return PermissionDecision(False, role=user.role, reason="角色不存在或已停用")
            if role.name == "admin":
                return PermissionDecision(True, role=role.name, reason="管理员访问")

            model = {"file": File, "task": Task}.get(resource)
            if model is None:
                return PermissionDecision(False, role=role.name, reason="未知资源类型")
            item = session.get(model, resource_id)
            owner_id = item.owner_id if resource == "file" and item else (
                item.user_id if item else None
            )
            if not owner_id or owner_id != user_id:
                return PermissionDecision(False, role=role.name, reason="资源不属于当前用户")
            return PermissionDecision(True, role=role.name, reason="资源所有权检查通过")
        except Exception as exc:
            logger.error("Database ownership check failed closed", exc_info=True)
            return PermissionDecision(False, reason=f"权限服务不可用: {type(exc).__name__}")
        finally:
            if session is not None:
                session.close()
