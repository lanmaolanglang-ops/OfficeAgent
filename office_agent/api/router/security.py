"""Administrative database-RBAC endpoints."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ...database.models import PermissionModel, RoleModel

router = APIRouter(prefix="/api/security", tags=["安全管理"])
_ROLE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _get_session():
    from ...database.session import SessionLocal
    return SessionLocal()


class RolePermissionsUpdate(BaseModel):
    permissions: list[str] = Field(min_length=1, max_length=128)

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 128 for item in normalized):
            raise ValueError("权限名不能为空且不得超过 128 字符")
        if len(set(normalized)) != len(normalized):
            raise ValueError("权限列表不得重复")
        return normalized


@router.get("/roles")
def list_roles():
    """List active and disabled roles from the authoritative database."""
    session = _get_session()
    try:
        roles = session.scalars(select(RoleModel).order_by(RoleModel.name)).all()
        return {"success": True, "data": [role.to_dict() for role in roles]}
    finally:
        session.close()


@router.get("/permissions")
def list_permissions():
    """List registered permissions available for role assignment."""
    session = _get_session()
    try:
        permissions = session.scalars(
            select(PermissionModel).order_by(PermissionModel.name)
        ).all()
        return {
            "success": True,
            "data": [permission.to_dict() for permission in permissions],
        }
    finally:
        session.close()


@router.put("/roles/{role_name}/permissions")
def update_role_permissions(role_name: str, update: RolePermissionsUpdate):
    """Atomically replace a role's permissions after registry validation."""
    if not _ROLE_NAME.fullmatch(role_name):
        raise HTTPException(status_code=400, detail="角色名格式无效")
    requested = set(update.permissions)
    if role_name == "admin" and "admin:user" not in requested:
        raise HTTPException(status_code=400, detail="管理员角色必须保留 admin:user 权限")

    session = _get_session()
    try:
        role = session.scalar(select(RoleModel).where(RoleModel.name == role_name))
        if role is None:
            raise HTTPException(status_code=404, detail="角色不存在")
        registered = set(session.scalars(select(PermissionModel.name).where(
            PermissionModel.name.in_(requested)
        )).all())
        unknown = requested - registered
        if unknown:
            raise HTTPException(
                status_code=400,
                detail="未注册权限: " + ", ".join(sorted(unknown)),
            )
        role.permissions = sorted(requested)
        session.commit()
        session.refresh(role)
        return {"success": True, "data": role.to_dict()}
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
