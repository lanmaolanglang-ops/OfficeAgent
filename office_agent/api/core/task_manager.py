"""
任务管理器 - 内存版，预留数据库/队列接口
"""
import uuid
import time
import threading
from typing import Dict, List, Optional
from datetime import datetime, timezone
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task:
    """任务对象"""
    def __init__(self, task_type: str, instruction: str,
                 agent: str = "", file_ids: List[str] = None,
                 options: Dict = None):
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
        }

    def start(self):
        self.status = TaskStatus.RUNNING.value
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._start_time = time.time()

    def update(self, progress: int = None, step: str = None,
               status: str = None):
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
            self.status = status

    def complete(self, result: Dict = None, output_files: List[str] = None,
                 quality_score: float = None):
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

    def list_tasks(self, status: str = None, agent: str = None,
                   page: int = 1, page_size: int = 20) -> tuple:
        tasks = list(self.tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        if agent:
            tasks = [t for t in tasks if t.agent == agent]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        total = len(tasks)
        start = (page - 1) * page_size
        return tasks[start:start + page_size], total

    def get_active_count(self) -> int:
        return sum(1 for t in self.tasks.values()
                   if t.status in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value))


# 全局任务管理器
task_manager = TaskManager()
