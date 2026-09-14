"""任务模型"""
import uuid
from datetime import datetime
from sqlalchemy import (
    String, Integer, Text, ForeignKey, DateTime, Float, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin


def _uuid():
    return f"task_{uuid.uuid4().hex[:12]}"


class Task(Base, TimestampMixin):
    """任务表"""
    # One parent may not carry two revisions with the same number (P3-10).
    # Root tasks have parent_task_id NULL; SQL treats NULLs as distinct, so
    # they never collide.
    __table_args__ = (
        UniqueConstraint("parent_task_id", "revision_number",
                         name="uq_task_parent_revision"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # word_format/ppt_generate/excel_analysis/general
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    # pending/queued/running/success/failed/cancelled
    agent_name: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    parent_task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # 进度
    progress: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str] = mapped_column(String(256), nullable=True)

    # 文件关联
    input_file_ids: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array
    output_file_ids: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array

    # 用户
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 会话：结构化字段，供 follow-up 精确查询（P2-18）；旧数据可为 NULL
    conversation_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    # 结果和错误
    result_json: Mapped[str] = mapped_column(Text, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)

    # 选项
    options_json: Mapped[str] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)

    # 时间
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    # 质量
    quality_score: Mapped[float] = mapped_column(Float, nullable=True)
    feedback_rating: Mapped[int] = mapped_column(Integer, nullable=True)
    feedback_comment: Mapped[str] = mapped_column(Text, nullable=True)

    # 回调
    callback_url: Mapped[str] = mapped_column(String(512), nullable=True)

    # 关系
    user = relationship("User", back_populates="tasks")
    parent_task = relationship("Task", remote_side=[id], backref="revisions", passive_deletes=True)
    logs = relationship(
        "ExecutionLog", back_populates="task", lazy="dynamic", passive_deletes=True
    )

    def __repr__(self):
        return f"<Task {self.id} [{self.status}] {self.task_type}>"
