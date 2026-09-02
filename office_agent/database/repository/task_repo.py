"""任务 Repository"""
from typing import List
from sqlalchemy import select, update, func
from sqlalchemy.orm import Session
from ..time import utc_now

from .base import BaseRepository
from ..models.task import Task


class TaskRepository(BaseRepository[Task]):
    def __init__(self, session: Session):
        super().__init__(session, Task)

    def get_by_user(self, user_id: str, offset: int = 0, limit: int = 100) -> List[Task]:
        return self.find(offset=offset, limit=limit, user_id=user_id)

    def get_by_status(self, status: str, offset: int = 0, limit: int = 100) -> List[Task]:
        return self.find(offset=offset, limit=limit, status=status)

    def get_by_agent(self, agent_name: str, offset: int = 0, limit: int = 100) -> List[Task]:
        return self.find(offset=offset, limit=limit, agent_name=agent_name)

    def get_recent(self, limit: int = 20, offset: int = 0) -> List[Task]:
        """Return newest tasks for conversation-context recovery."""
        offset, limit = self._page(offset, limit)
        stmt = select(Task).order_by(Task.created_at.desc()).offset(offset).limit(limit)
        return list(self.session.scalars(stmt))

    def get_pending_tasks(self, limit: int = 10) -> List[Task]:
        return self.get_by_status("pending", limit=limit)

    def get_active_tasks(self) -> List[Task]:
        stmt = select(Task).where(Task.status.in_(["pending", "running"]))
        return list(self.session.scalars(stmt))

    def create_task(self, task_type: str, instruction: str, agent_name: str = None,
                    user_id: str = None, input_file_ids: str = None,
                    options_json: str = None, priority: int = 0,
                    callback_url: str = None, parent_task_id: str = None,
                    revision_number: int = 1) -> Task:
        task = Task(
            task_type=task_type,
            instruction=instruction,
            agent_name=agent_name,
            user_id=user_id,
            input_file_ids=input_file_ids,
            options_json=options_json,
            priority=priority,
            callback_url=callback_url,
            parent_task_id=parent_task_id,
            revision_number=revision_number,
            status="pending",
        )
        return self.create(task)

    def start_task(self, task_id: str):
        result = self.session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status.in_(("pending", "queued")))
            .values(
                status="running",
                started_at=func.coalesce(Task.started_at, utc_now()),
            )
            .execution_options(synchronize_session="fetch")
        )
        return bool(result.rowcount)

    def update_progress(self, task_id: str, progress: int, current_step: str = None):
        data = {"progress": progress}
        if current_step is not None:
            data["current_step"] = current_step
        self.update(task_id, data)

    def complete_task(self, task_id: str, result_json: str = None,
                      output_file_ids: str = None, quality_score: float = None,
                      duration_ms: int = None):
        data = {
            "status": "success",
            "progress": 100,
            "finished_at": utc_now(),
        }
        if result_json:
            data["result_json"] = result_json
        if output_file_ids:
            data["output_file_ids"] = output_file_ids
        if quality_score is not None:
            data["quality_score"] = quality_score
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        result = self.session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status.notin_(("success", "failed", "cancelled")))
            .values(**data)
            .execution_options(synchronize_session="fetch")
        )
        return bool(result.rowcount)

    def fail_task(self, task_id: str, error_message: str, duration_ms: int = None):
        data = {
            "status": "failed",
            "progress": 100,
            "current_step": "处理失败",
            "error_message": error_message,
            "finished_at": utc_now(),
        }
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        result = self.session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status.notin_(("success", "failed", "cancelled")))
            .values(**data)
            .execution_options(synchronize_session="fetch")
        )
        return bool(result.rowcount)

    def cancel_task(self, task_id: str):
        """取消任务：仅当任务仍处于活动状态时生效，避免把已完成任务改写为 cancelled"""
        result = self.session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status.in_(("pending", "queued", "running")))
            .values(status="cancelled", finished_at=utc_now())
            .execution_options(synchronize_session="fetch")
        )
        return bool(result.rowcount)

    def add_feedback(self, task_id: str, rating: int, comment: str = None):
        data = {"feedback_rating": rating}
        if comment:
            data["feedback_comment"] = comment
        self.update(task_id, data)
