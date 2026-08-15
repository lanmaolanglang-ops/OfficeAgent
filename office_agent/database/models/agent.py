"""Agent配置模型"""
import uuid
from sqlalchemy import String, Boolean, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin


def _uuid():
    return f"agent_{uuid.uuid4().hex[:12]}"


class AgentConfig(Base, TimestampMixin):
    """Agent配置表"""
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # word_agent/ppt_agent/excel_agent/orchestrator
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # word/ppt/excel/orchestrator
    description: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    capabilities: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array
    model_config: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    prompt_template: Mapped[str] = mapped_column(Text, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="available")
    priority: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self):
        return f"<Agent {self.agent_id} v{self.version}>"
