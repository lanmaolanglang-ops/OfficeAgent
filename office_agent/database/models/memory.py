"""长期记忆模型"""
import uuid
from sqlalchemy import String, Text, Integer, Float, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin


def _uuid():
    return f"mem_{uuid.uuid4().hex[:12]}"


class Memory(Base, TimestampMixin):
    """长期记忆表"""
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("user.id"), nullable=True, index=True
    )
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # preference/habit/workflow/fact/feedback
    key: Mapped[str] = mapped_column(String(128), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    # 0.0 - 1.0
    category: Mapped[str] = mapped_column(String(64), nullable=True)
    # format/style/template/agent/model
    tags: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array
    source: Mapped[str] = mapped_column(String(64), nullable=True)
    # user_feedback/auto_learned/manual
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 关系
    user = relationship("User", back_populates="memories")

    def __repr__(self):
        return f"<Memory {self.memory_type}: {self.content[:30]}...>"
