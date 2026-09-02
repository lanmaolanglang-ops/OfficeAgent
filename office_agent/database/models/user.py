"""用户模型"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, Boolean, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin


def _uuid():
    return uuid.uuid4().hex[:16]


class User(Base, TimestampMixin):
    """用户表"""
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="user")  # user/admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar: Mapped[str] = mapped_column(String(256), nullable=True)
    last_login: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_ip: Mapped[str] = mapped_column(String(64), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    preferences: Mapped[str] = mapped_column(Text, nullable=True)  # JSON

    # 关系
    files = relationship("File", back_populates="owner", lazy="dynamic")
    tasks = relationship("Task", back_populates="user", lazy="dynamic")
    audit_logs = relationship(
        "AuditLogModel", back_populates="user", passive_deletes=True
    )
    api_keys = relationship(
        "APIKeyModel", back_populates="user", cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self):
        return f"<User {self.username}>"
