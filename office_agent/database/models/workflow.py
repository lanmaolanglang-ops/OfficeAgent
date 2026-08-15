"""
Workflow 数据库模型
WorkflowInstance 和 AgentMessage 表
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime, JSON, ForeignKey, Index,
)
from sqlalchemy.orm import relationship

from ..base import Base


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class TimestampMixin:
    """时间戳混入"""
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WorkflowInstanceModel(Base, TimestampMixin):
    """工作流实例表"""
    __tablename__ = "workflow_instances"

    id = Column(String(32), primary_key=True, default=lambda: _gen_id("wf"))
    name = Column(String(200), nullable=False, default="")
    workflow_type = Column(String(50), nullable=False, default="custom")
    description = Column(Text, default="")
    status = Column(String(30), nullable=False, default="pending")

    # 用户输入
    user_request = Column(Text, default="")
    input_files = Column(JSON, default=list)  # file_id列表
    params = Column(JSON, default=dict)

    # DAG结构
    tasks = Column(JSON, default=dict)  # task_id -> task_info
    task_order = Column(JSON, default=list)
    current_step = Column(Integer, default=0)

    # 上下文（JSON序列化）
    context_data = Column(JSON, default=dict)

    # 结果
    output_files = Column(JSON, default=list)
    final_result = Column(JSON, nullable=True)
    errors = Column(JSON, default=list)

    # 时间
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, default=0)

    # 用户
    user_id = Column(String(64), nullable=True, index=True)

    # 统计
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    failed_tasks = Column(Integer, default=0)

    # 模板
    template_id = Column(String(50), nullable=True)

    # 关系
    messages = relationship(
        "AgentMessageModel",
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="AgentMessageModel.created_at",
    )

    __table_args__ = (
        Index("idx_wf_status", "status"),
        Index("idx_wf_type", "workflow_type"),
        Index("idx_wf_user", "user_id"),
        Index("idx_wf_created", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "workflow_type": self.workflow_type,
            "status": self.status,
            "user_request": self.user_request,
            "input_files": self.input_files or [],
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "output_files": self.output_files or [],
            "errors": self.errors or [],
            "duration_ms": self.duration_ms,
            "template_id": self.template_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class AgentMessageModel(Base, TimestampMixin):
    """Agent消息表"""
    __tablename__ = "agent_messages"

    id = Column(String(32), primary_key=True, default=lambda: _gen_id("msg"))
    workflow_id = Column(String(32), ForeignKey("workflow_instances.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    from_agent = Column(String(30), nullable=False)
    to_agent = Column(String(30), nullable=False)
    message_type = Column(String(30), nullable=False)
    content = Column(JSON, nullable=True)
    task_id = Column(String(32), nullable=True, index=True)

    read = Column(Boolean, default=False)

    # 关系
    workflow = relationship("WorkflowInstanceModel", back_populates="messages")

    __table_args__ = (
        Index("idx_msg_workflow", "workflow_id"),
        Index("idx_msg_from", "from_agent"),
        Index("idx_msg_to", "to_agent"),
        Index("idx_msg_type", "message_type"),
        Index("idx_msg_task", "task_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "message_type": self.message_type,
            "content": self.content,
            "task_id": self.task_id,
            "read": self.read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
