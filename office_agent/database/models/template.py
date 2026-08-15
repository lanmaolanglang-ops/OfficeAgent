"""模板模型"""
import uuid
from sqlalchemy import String, Text, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin


def _uuid():
    return f"tpl_{uuid.uuid4().hex[:12]}"


class Template(Base, TimestampMixin):
    """模板表"""
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    template_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # word/ppt/excel
    file_path: Mapped[str] = mapped_column(String(512), nullable=True)
    file_id: Mapped[str] = mapped_column(String(32), nullable=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=True)
    # 论文/公文/商业/学术等
    tags: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array
    preview_image: Mapped[str] = mapped_column(String(512), nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")

    def __repr__(self):
        return f"<Template {self.name} ({self.template_type})>"
