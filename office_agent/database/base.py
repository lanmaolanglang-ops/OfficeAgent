"""
SQLAlchemy ORM 基类
"""
from datetime import datetime
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, declared_attr

from .time import utc_now


class Base(DeclarativeBase):
    """所有ORM模型的基类"""

    # 自动表名（类名转蛇形）
    @declared_attr.directive
    def __tablename__(cls) -> str:
        import re
        name = cls.__name__
        # CamelCase -> snake_case
        s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


class TimestampMixin:
    """时间戳混入。

    Python 路径 ``default=utc_now`` / ``onupdate=utc_now`` 统一 aware UTC；
    ``server_default=func.now()`` 仅作 raw-SQL/legacy 插入的兼容兜底（P2-32）。
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now,
        server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now,
        server_default=func.now(), nullable=False,
    )
