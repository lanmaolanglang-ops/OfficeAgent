"""PPT 相关数据库模型"""
import uuid
from sqlalchemy import String, Text, Integer, Boolean, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin


def _ppt_template_id():
    return f"ppt_tpl_{uuid.uuid4().hex[:12]}"


def _slide_history_id():
    return f"slide_{uuid.uuid4().hex[:12]}"


def _ppt_generation_id():
    return f"ppt_gen_{uuid.uuid4().hex[:12]}"


class PPTTemplate(Base, TimestampMixin):
    """PPT 模板表"""
    __tablename__ = "ppt_template"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_ppt_template_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    style: Mapped[str] = mapped_column(String(32), nullable=False, default="business")
    # business/tech/education/defense/product/minimal/creative
    template_path: Mapped[str] = mapped_column(String(512), nullable=True)
    file_id: Mapped[str] = mapped_column(String(32), nullable=True)
    preview_image: Mapped[str] = mapped_column(String(512), nullable=True)

    # 布局配置 JSON
    layout_config: Mapped[str] = mapped_column(Text, nullable=True)
    # 主题配置 JSON（颜色/字体）
    theme_config: Mapped[str] = mapped_column(Text, nullable=True)
    # 颜色规范
    color_scheme: Mapped[str] = mapped_column(Text, nullable=True)
    # 字体规则
    font_rules: Mapped[str] = mapped_column(Text, nullable=True)

    # 适用场景
    scenario: Mapped[str] = mapped_column(String(64), nullable=True)
    # business_report/product_launch/academic_defense/training/proposal/data_report
    description: Mapped[str] = mapped_column(Text, nullable=True)
    tags: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array

    # 统计
    usage_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rating: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", server_default="1.0.0")

    def __repr__(self):
        return f"<PPTTemplate {self.name} ({self.style})>"


class SlideHistory(Base, TimestampMixin):
    """幻灯片历史表 - 保存生成/修改记录和版本"""
    __tablename__ = "slide_history"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_slide_history_id)
    generation_id: Mapped[str] = mapped_column(String(32), nullable=True, index=True)
    # 关联的生成任务

    # 基本信息
    slide_index: Mapped[int] = mapped_column(Integer, nullable=False)
    slide_type: Mapped[str] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(256), nullable=True)

    # 内容
    content_text: Mapped[str] = mapped_column(Text, nullable=True)
    content_json: Mapped[str] = mapped_column(Text, nullable=True)  # 结构化内容
    speaker_notes: Mapped[str] = mapped_column(Text, nullable=True)

    # 布局和设计
    layout_config: Mapped[str] = mapped_column(Text, nullable=True)
    design_style: Mapped[str] = mapped_column(String(32), nullable=True)

    # 版本
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    action: Mapped[str] = mapped_column(String(32), default="created", server_default="created")
    # created/modified/regenerated/deleted

    # 关联
    user_id: Mapped[str] = mapped_column(String(32), nullable=True)
    presentation_id: Mapped[str] = mapped_column(String(32), nullable=True)

    def __repr__(self):
        return f"<SlideHistory page={self.slide_index} v{self.version}>"


class PPTGeneration(Base, TimestampMixin):
    """PPT 生成记录表"""
    __tablename__ = "ppt_generation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_ppt_generation_id)
    topic: Mapped[str] = mapped_column(String(256), nullable=False)
    scenario: Mapped[str] = mapped_column(String(64), nullable=True)
    style: Mapped[str] = mapped_column(String(32), nullable=True)

    # 输入
    outline: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    template_id: Mapped[str] = mapped_column(String(32), nullable=True)
    data_file_id: Mapped[str] = mapped_column(String(32), nullable=True)

    # 输出
    output_file_id: Mapped[str] = mapped_column(String(32), nullable=True)
    output_path: Mapped[str] = mapped_column(String(512), nullable=True)
    slide_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # 质量
    quality_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    quality_report: Mapped[str] = mapped_column(Text, nullable=True)

    # 状态
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    # pending/generating/completed/failed
    error: Mapped[str] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # 用户
    user_id: Mapped[str] = mapped_column(String(32), nullable=True)

    def __repr__(self):
        return f"<PPTGeneration {self.topic} ({self.status})>"
