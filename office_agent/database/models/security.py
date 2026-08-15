"""
Security Database Models - 安全相关数据库模型
User, Role, Permission, AuditLog
"""
from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Boolean, Integer, Float,
    DateTime, ForeignKey, JSON, Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from ..base import Base


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class TimestampMixin:
    """时间戳混入"""
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserModel(Base, TimestampMixin):
    """用户表"""
    __tablename__ = "security_users"

    id = Column(String(32), primary_key=True, default=lambda: _gen_id("usr"))
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), unique=True, nullable=True, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(32), default="user", nullable=False)  # admin/user/guest
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False)
    last_login = Column(DateTime, nullable=True)
    last_login_ip = Column(String(64), nullable=True)
    failed_login_count = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    extra = Column(JSON, default=dict)

    # 关系
    audit_logs = relationship("AuditLogModel", back_populates="user")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RoleModel(Base, TimestampMixin):
    """角色表"""
    __tablename__ = "security_roles"

    id = Column(String(32), primary_key=True, default=lambda: _gen_id("role"))
    name = Column(String(64), unique=True, nullable=False)
    display_name = Column(String(128))
    description = Column(Text)
    permissions = Column(JSON, default=list)  # 权限列表 ["file:read", "task:create", ...]
    is_system = Column(Boolean, default=False)  # 系统角色不可删除
    is_active = Column(Boolean, default=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "permissions": self.permissions or [],
            "is_system": self.is_system,
            "is_active": self.is_active,
        }


class PermissionModel(Base, TimestampMixin):
    """权限表"""
    __tablename__ = "security_permissions"

    id = Column(String(32), primary_key=True, default=lambda: _gen_id("perm"))
    name = Column(String(128), unique=True, nullable=False)  # e.g. "file:read"
    resource = Column(String(64), nullable=False)  # file, task, agent...
    action = Column(String(64), nullable=False)  # read, write, execute...
    description = Column(Text)
    risk_level = Column(String(16), default="low")  # low/medium/high/critical

    __table_args__ = (
        Index("idx_perm_resource_action", "resource", "action"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "resource": self.resource,
            "action": self.action,
            "description": self.description,
            "risk_level": self.risk_level,
        }


class AuditLogModel(Base, TimestampMixin):
    """审计日志表"""
    __tablename__ = "security_audit_logs"

    id = Column(String(32), primary_key=True, default=lambda: _gen_id("audit"))
    user_id = Column(String(32), ForeignKey("security_users.id"), nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)  # login, access_denied, file_upload...
    resource = Column(String(128))  # 操作的资源
    resource_id = Column(String(64))
    status = Column(String(16), nullable=False)  # success/denied/error
    ip_address = Column(String(64))
    user_agent = Column(String(512))
    details = Column(JSON, default=dict)
    risk_level = Column(String(16), default="info")  # info/warning/danger/critical
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # 关系
    user = relationship("UserModel", back_populates="audit_logs")

    __table_args__ = (
        Index("idx_audit_user_time", "user_id", "timestamp"),
        Index("idx_audit_action_status", "action", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "action": self.action,
            "resource": self.resource,
            "resource_id": self.resource_id,
            "status": self.status,
            "ip_address": self.ip_address,
            "details": self.details or {},
            "risk_level": self.risk_level,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class APIKeyModel(Base, TimestampMixin):
    """API Key表"""
    __tablename__ = "security_api_keys"

    id = Column(String(32), primary_key=True, default=lambda: _gen_id("key"))
    user_id = Column(String(32), ForeignKey("security_users.id"), nullable=False, index=True)
    key_hash = Column(String(256), nullable=False, unique=True)
    key_prefix = Column(String(16), index=True)  # 显示前缀
    name = Column(String(128))
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    expires_at = Column(DateTime, nullable=True)
    last_used = Column(DateTime, nullable=True)
    use_count = Column(Integer, default=0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "key_prefix": self.key_prefix,
            "name": self.name,
            "description": self.description,
            "is_active": self.is_active,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "use_count": self.use_count,
        }


class LoginAttemptModel(Base, TimestampMixin):
    """登录尝试记录"""
    __tablename__ = "security_login_attempts"

    id = Column(String(32), primary_key=True, default=lambda: _gen_id("login"))
    username = Column(String(64), nullable=False, index=True)
    ip_address = Column(String(64))
    success = Column(Boolean, default=False)
    failure_reason = Column(String(128))
    user_agent = Column(String(512))
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("idx_login_username_time", "username", "timestamp"),
        Index("idx_login_ip_time", "ip_address", "timestamp"),
    )
