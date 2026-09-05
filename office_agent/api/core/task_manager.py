"""
任务管理器 - 内存版，预留数据库/队列接口
"""
import uuid
import time
import threading
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError


logger = logging.getLogger("office_agent.api.task_manager")


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    # Compatibility alias: old callers used WAITING for the queue state.
    WAITING = "queued"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


def normalize_task_status(status: str | TaskStatus) -> str:
    """Return the canonical task status while accepting legacy waiting."""
    value = status.value if isinstance(status, TaskStatus) else str(status)
    if value == "waiting":
        return TaskStatus.QUEUED.value
    allowed = {item.value for item in TaskStatus}
    if value not in allowed:
        raise ValueError(f"未知任务状态: {value}")
    return value


class Task:
    """任务对象"""
    def __init__(self, task_type: str, instruction: str,
                 agent: str = "", file_ids: List[str] | None = None,
                 options: Dict | None = None):
        self.task_id = f"task_{uuid.uuid4().hex[:12]}"
        self.task_type = task_type
        self.instruction = instruction
        self.agent = agent
        self.file_ids = file_ids or []
        self.options = options or {}
        self.status = TaskStatus.PENDING.value
        self.progress = 0
        self.current_step = ""
        self.steps: List[Dict] = []
        self.input_files: List[str] = list(file_ids or [])
        self.output_files: List[str] = []
        self.result: Optional[Dict] = None
        self.error: Optional[str] = None
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.duration_ms: Optional[int] = None
        self.quality_score: Optional[float] = None
        self.feedback_rating: Optional[int] = None
        self.feedback_comment: Optional[str] = None
        # 数据存储权威标记：persisted 表示数据库记录，memory 表示仅内存。
        # degraded=True 只用于数据库不可用时的临时权威快照。
        self.storage = "persisted"
        self.degraded = False
        self._start_time: Optional[float] = None

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "agent": self.agent,
            "status": self.status,
            "progress": self.progress,
            "current_step": self.current_step,
            "steps": self.steps,
            "input_files": self.input_files,
            "output_files": self.output_files,
            "instruction": self.instruction,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "quality_score": self.quality_score,
            "feedback_rating": self.feedback_rating,
            "feedback_comment": self.feedback_comment,
            "storage": self.storage,
            "degraded": self.degraded,
        }

    def start(self):
        self.status = TaskStatus.RUNNING.value
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._start_time = time.time()

    def update(self, progress: int | None = None, step: str | None = None,
               status: str | None = None):
        if progress is not None:
            self.progress = min(100, max(0, progress))
        if step is not None:
            self.current_step = step
            self.steps.append({
                "step_id": f"step_{len(self.steps)}",
                "name": step,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            })
        if status is not None:
            self.status = normalize_task_status(status)

    def complete(self, result: Dict | None = None, output_files: List[str] | None = None,
                 quality_score: float | None = None):
        self.status = TaskStatus.SUCCESS.value
        self.progress = 100
        self.current_step = "完成"
        self.result = result or {}
        if output_files:
            self.output_files = output_files
        if quality_score is not None:
            self.quality_score = quality_score
        self.completed_at = datetime.now(timezone.utc).isoformat()
        if self._start_time:
            self.duration_ms = int((time.time() - self._start_time) * 1000)
        # 标记最后一步完成
        if self.steps:
            self.steps[-1]["status"] = "completed"
            self.steps[-1]["completed_at"] = self.completed_at

    def fail(self, error: str):
        self.status = TaskStatus.FAILED.value
        self.error = error
        self.current_step = "失败"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        if self._start_time:
            self.duration_ms = int((time.time() - self._start_time) * 1000)
        if self.steps:
            self.steps[-1]["status"] = "failed"
            self.steps[-1]["completed_at"] = self.completed_at


class TaskManager:
    """任务管理器（内存版）"""

    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def list_tasks(self, status: str | None = None, agent: str | None = None,
                   page: int = 1, page_size: int = 20) -> tuple:
        # 与 API 输入契约（api/core/pagination.py）同语义的防御性校验
        from .pagination import validate_page
        validate_page(page, page_size)
        tasks = list(self.tasks.values())
        if status:
            normalized_status = normalize_task_status(status)
            tasks = [t for t in tasks if t.status == normalized_status]
        if agent:
            tasks = [t for t in tasks if t.agent == agent]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        total = len(tasks)
        start = (page - 1) * page_size
        return tasks[start:start + page_size], total

    def get_active_count(self) -> int:
        return sum(1 for t in self.tasks.values()
                   if t.status in {
                       TaskStatus.PENDING.value,
                       TaskStatus.QUEUED.value,
                       TaskStatus.RUNNING.value,
                   })

    def create_memory_task(self, task_id: str | None = None, task_type: str = "unknown",
                           instruction: str = "", agent: str = "",
                           file_ids: List[str] | None = None, options: Dict | None = None,
                           user_id: str | None = None, status: str | None = None) -> Task:
        """Create or refresh a process-local degraded task snapshot.

        This is deliberately not a general-purpose task factory. It is only
        used after the authoritative database path is unavailable, and it
        marks the task as memory-only so callers never mistake it for a
        persisted record.
        """
        with self._lock:
            task = self.tasks.get(task_id) if task_id else None
            if task is None:
                task = Task(
                    task_type=task_type,
                    instruction=instruction,
                    agent=agent,
                    file_ids=file_ids,
                    options=options,
                )
                if task_id:
                    task.task_id = task_id
            task.storage = "memory"
            task.degraded = True
            if user_id is not None:
                setattr(task, "user_id", user_id)
            if status is not None:
                task.update(status=status)
            self.tasks[task.task_id] = task
            return task

    def update_memory_task(self, task_id: str, **fields) -> Optional[Task]:
        """Update a degraded in-memory snapshot without touching the DB."""
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None
            for key, value in fields.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            task.storage = "memory"
            task.degraded = True
            return task

    def cancel_memory_task(self, task_id: str) -> bool:
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                return False
            task.status = TaskStatus.CANCELLED.value
            task.completed_at = datetime.now(timezone.utc).isoformat()
            return True

    def snapshot_tasks(self, status: str | None = None, agent: str | None = None,
                       user_id: str | None = None) -> List[Task]:
        """Return filtered degraded snapshots without pagination."""
        with self._lock:
            tasks = list(self.tasks.values())
        if status:
            normalized_status = normalize_task_status(status)
            tasks = [t for t in tasks if t.status == normalized_status]
        if agent:
            tasks = [t for t in tasks if t.agent == agent]
        if user_id:
            tasks = [t for t in tasks if getattr(t, "user_id", None) == user_id]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks


def normalize_db_failure_category(exc: BaseException | None) -> str:
    """Return a stable, non-sensitive category for DB fallback logs."""
    if isinstance(exc, OperationalError):
        return "operational_error"
    if isinstance(exc, DBAPIError):
        return "dbapi_error"
    if isinstance(exc, SQLAlchemyError):
        return "sqlalchemy_error"
    return type(exc).__name__


def log_db_fallback(operation: str, task_id: str | None = None,
                    exc: BaseException | None = None) -> None:
    """Emit the one structured fallback log used by task management paths."""
    logger.warning(
        "TaskManager database fallback",
        extra={
            "operation": operation,
            "task_id": task_id,
            "degraded_mode": True,
            "normalized_failure_category": normalize_db_failure_category(exc),
            "failure_type": type(exc).__name__ if exc else None,
        },
    )


# 全局任务管理器
task_manager = TaskManager()
