"""安全模型，统一使用应用主 ``user`` 表作为认证真相源。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin
from ..time import utc_now
from .user import User

UserModel = User


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class RoleModel(Base, TimestampMixin):
    __tablename__ = "security_roles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _gen_id("role"))
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "display_name": self.display_name,
                "description": self.description, "permissions": self.permissions or [],
                "is_system": self.is_system, "is_active": self.is_active}


class PermissionModel(Base, TimestampMixin):
    __tablename__ = "security_permissions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _gen_id("perm"))
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    __table_args__ = (Index("idx_perm_resource_action", "resource", "action"),)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "resource": self.resource,
                "action": self.action, "description": self.description,
                "risk_level": self.risk_level}


class AuditLogModel(Base, TimestampMixin):
    __tablename__ = "security_audit_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _gen_id("audit"))
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource: Mapped[str | None] = mapped_column(String(128))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_level: Mapped[str] = mapped_column(String(16), default="info")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    user = relationship("User", back_populates="audit_logs")
    __table_args__ = (
        Index("idx_audit_user_time", "user_id", "timestamp"),
        Index("idx_audit_action_status", "action", "status"),
    )

    def to_dict(self) -> dict:
        return {"id": self.id, "user_id": self.user_id, "action": self.action,
                "resource": self.resource, "resource_id": self.resource_id,
                "status": self.status, "ip_address": self.ip_address,
                "details": self.details or {}, "risk_level": self.risk_level,
                "timestamp": self.timestamp.isoformat() if self.timestamp else None}


class APIKeyModel(Base, TimestampMixin):
    __tablename__ = "security_api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _gen_id("key"))
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    user = relationship("User", back_populates="api_keys")

    def to_dict(self) -> dict:
        return {"id": self.id, "user_id": self.user_id, "key_prefix": self.key_prefix,
                "name": self.name, "description": self.description,
                "is_active": self.is_active,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "last_used": self.last_used.isoformat() if self.last_used else None,
                "use_count": self.use_count}


class LoginAttemptModel(Base, TimestampMixin):
    __tablename__ = "security_login_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _gen_id("login"))
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_reason: Mapped[str | None] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    __table_args__ = (
        Index("idx_login_username_time", "username", "timestamp"),
        Index("idx_login_ip_time", "ip_address", "timestamp"),
    )
