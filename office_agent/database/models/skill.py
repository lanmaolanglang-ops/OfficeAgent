"""技能模型"""
import uuid
from sqlalchemy import String, Boolean, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin


def _uuid():
    return f"skill_{uuid.uuid4().hex[:12]}"


class Skill(Base, TimestampMixin):
    """技能表"""
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    skill_type: Mapped[str] = mapped_column(String(64), nullable=True)
    # 论文排版/商业PPT/数据分析等
    description: Mapped[str] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    category: Mapped[str] = mapped_column(String(64), nullable=True)
    # word/ppt/excel/general
    tags: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array
    config_json: Mapped[str] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # JSON array containing word/excel/ppt/chat/all.  Kept explicit instead of
    # overloading generic tags so resolution stays deterministic.
    target_agents: Mapped[str] = mapped_column(Text, default='["all"]')
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    source: Mapped[str] = mapped_column(String(32), default="ui")
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self):
        return f"<Skill {self.name} v{self.version}>"
