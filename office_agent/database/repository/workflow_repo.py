"""
Workflow Repository - 工作流数据访问层
"""
from __future__ import annotations

from typing import Optional
from contextlib import contextmanager

from sqlalchemy import desc

from .base import BaseRepository
from ..models.workflow import WorkflowInstanceModel, AgentMessageModel
from ..session import SessionLocal


class _SessionMixin:
    """提供get_session上下文管理器"""

    @contextmanager
    def get_session(self):
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class WorkflowRepository(BaseRepository[WorkflowInstanceModel], _SessionMixin):
    """工作流实例Repository"""

    def __init__(self, session_factory=None):
        super().__init__(WorkflowInstanceModel, session_factory)

    def find_by_user(self, user_id: str, limit: int = 50) -> list[WorkflowInstanceModel]:
        with self.get_session() as session:
            return session.query(WorkflowInstanceModel)\
                .filter(WorkflowInstanceModel.user_id == user_id)\
                .order_by(desc(WorkflowInstanceModel.created_at))\
                .limit(limit).all()

    def find_by_status(self, status: str) -> list[WorkflowInstanceModel]:
        with self.get_session() as session:
            return session.query(WorkflowInstanceModel)\
                .filter(WorkflowInstanceModel.status == status)\
                .order_by(desc(WorkflowInstanceModel.created_at)).all()

    def find_by_type(self, workflow_type: str) -> list[WorkflowInstanceModel]:
        with self.get_session() as session:
            return session.query(WorkflowInstanceModel)\
                .filter(WorkflowInstanceModel.workflow_type == workflow_type)\
                .order_by(desc(WorkflowInstanceModel.created_at)).all()

    def find_running(self) -> list[WorkflowInstanceModel]:
        return self.find_by_status("running")

    def find_pending_approval(self) -> list[WorkflowInstanceModel]:
        return self.find_by_status("waiting_approval")

    def update_status(self, workflow_id: str, status: str, **kwargs) -> bool:
        with self.get_session() as session:
            obj = session.query(WorkflowInstanceModel).get(workflow_id)
            if not obj:
                return False
            if status is not None:
                obj.status = status
            for k, v in kwargs.items():
                if hasattr(obj, k) and v is not None:
                    setattr(obj, k, v)
            session.commit()
            return True

    def save_result(self, workflow_id: str, result: dict) -> bool:
        """保存工作流结果"""
        with self.get_session() as session:
            obj = session.query(WorkflowInstanceModel).get(workflow_id)
            if not obj:
                return False
            obj.final_result = result
            obj.status = result.get("success", False) and "completed" or "failed"
            obj.completed_tasks = result.get("completed_tasks", 0)
            obj.failed_tasks = result.get("failed_tasks", 0)
            obj.output_files = result.get("output_files", [])
            obj.errors = result.get("errors", [])
            session.commit()
            return True


class AgentMessageRepository(BaseRepository[AgentMessageModel], _SessionMixin):
    """Agent消息Repository"""

    def __init__(self, session_factory=None):
        super().__init__(AgentMessageModel, session_factory)

    def find_by_workflow(self, workflow_id: str) -> list[AgentMessageModel]:
        with self.get_session() as session:
            return session.query(AgentMessageModel)\
                .filter(AgentMessageModel.workflow_id == workflow_id)\
                .order_by(AgentMessageModel.created_at).all()

    def find_by_task(self, task_id: str) -> list[AgentMessageModel]:
        with self.get_session() as session:
            return session.query(AgentMessageModel)\
                .filter(AgentMessageModel.task_id == task_id)\
                .order_by(AgentMessageModel.created_at).all()

    def find_unread(self, to_agent: Optional[str] = None) -> list[AgentMessageModel]:
        with self.get_session() as session:
            q = session.query(AgentMessageModel)\
                .filter(AgentMessageModel.read == False)
            if to_agent:
                q = q.filter(AgentMessageModel.to_agent == to_agent)
            return q.order_by(AgentMessageModel.created_at).all()

    def mark_read(self, message_id: str) -> bool:
        with self.get_session() as session:
            obj = session.query(AgentMessageModel).get(message_id)
            if not obj:
                return False
            obj.read = True
            session.commit()
            return True

    def mark_all_read(self, workflow_id: str) -> int:
        with self.get_session() as session:
            count = session.query(AgentMessageModel)\
                .filter(
                    AgentMessageModel.workflow_id == workflow_id,
                    AgentMessageModel.read == False
                ).update({"read": True})
            session.commit()
            return count
