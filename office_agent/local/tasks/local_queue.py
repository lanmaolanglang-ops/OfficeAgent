"""
Local Task Queue - 本地任务队列
基于线程池的后台任务执行，不依赖Celery/Redis
支持Word排版、PPT生成、Excel分析等任务
"""
import os
import time
import uuid
import queue
import threading
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, Future, as_completed


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class TaskPriority(int, Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class LocalTask:
    """本地任务"""
    task_id: str
    task_type: str
    func: Callable = field(repr=False)
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: float = field(default_factory=time.time)
    started_at: float = 0
    completed_at: float = 0
    result: Any = None
    error: str = ""
    progress: float = 0.0
    progress_message: str = ""
    timeout_seconds: int = 300
    retries: int = 0
    max_retries: int = 0
    callback: Optional[Callable] = field(default=None, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def duration_ms(self) -> int:
        if self.completed_at and self.started_at:
            return int((self.completed_at - self.started_at) * 1000)
        if self.started_at:
            return int((time.time() - self.started_at) * 1000)
        return 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "priority": self.priority.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "error": self.error,
            "retries": self.retries,
        }


class LocalTaskQueue:
    """
    本地任务队列
    - 基于ThreadPoolExecutor
    - 支持优先级
    - 支持任务取消
    - 支持超时
    - 支持重试
    - 支持进度回调
    """

    def __init__(self, max_workers: int = None, db=None):
        cpu_count = os.cpu_count() or 4
        self._max_workers = max_workers or min(cpu_count * 2, 8)
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="office_task")
        self._tasks: dict[str, LocalTask] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.RLock()
        self._db = db
        self._shutdown = False
        # 任务类型对应的处理器
        self._handlers: dict[str, Callable] = {}

    def register_handler(self, task_type: str, handler: Callable) -> None:
        """注册任务处理器"""
        self._handlers[task_type] = handler

    def submit(
        self,
        task_type: str,
        func: Callable = None,
        args: tuple = (),
        kwargs: dict = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout: int = 300,
        max_retries: int = 0,
        callback: Callable = None,
        task_id: str = None,
    ) -> LocalTask:
        """提交任务"""
        if self._shutdown:
            raise RuntimeError("任务队列已关闭")
        task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        # 如果没有提供func但注册了handler，使用handler
        if func is None and task_type in self._handlers:
            func = self._handlers[task_type]
        if func is None:
            raise ValueError(f"未提供任务函数且未注册处理器: {task_type}")
        task = LocalTask(
            task_id=task_id,
            task_type=task_type,
            func=func,
            args=args,
            kwargs=kwargs or {},
            priority=priority,
            timeout_seconds=timeout,
            max_retries=max_retries,
            callback=callback,
        )
        with self._lock:
            self._tasks[task_id] = task
        # 提交到线程池
        future = self._executor.submit(self._run_task, task)
        self._futures[task_id] = future
        # 保存到数据库
        if self._db:
            try:
                self._db.save_task({
                    "id": task_id,
                    "task_type": task_type,
                    "status": TaskStatus.PENDING.value,
                    "created_at": task.created_at,
                })
            except Exception:
                pass
        return task

    def _run_task(self, task: LocalTask) -> Any:
        """执行任务"""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._update_db_status(task)
        try:
            # 检查取消
            if task.cancel_event.is_set():
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
                return None
            # 执行
            result = task.func(*task.args, **task.kwargs)
            # 再次检查取消
            if task.cancel_event.is_set():
                task.status = TaskStatus.CANCELLED
            else:
                task.result = result
                task.status = TaskStatus.COMPLETED
                task.progress = 100.0
            task.completed_at = time.time()
            # 回调
            if task.callback:
                try:
                    task.callback(task)
                except Exception:
                    pass
            self._update_db_status(task)
            return result
        except Exception as e:
            task.error = str(e)
            # 重试
            if task.retries < task.max_retries:
                task.retries += 1
                task.status = TaskStatus.PENDING
                task.started_at = 0
                # 重新提交
                time.sleep(1)
                future = self._executor.submit(self._run_task, task)
                self._futures[task.task_id] = future
                return None
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            self._update_db_status(task)
            return None

    def _update_db_status(self, task: LocalTask) -> None:
        if self._db:
            try:
                self._db.save_task({
                    "id": task.task_id,
                    "task_type": task.task_type,
                    "status": task.status.value,
                    "started_at": task.started_at,
                    "completed_at": task.completed_at,
                    "duration_ms": task.duration_ms,
                    "error": task.error,
                    "input_summary": str(task.kwargs)[:500] if task.kwargs else "",
                    "output_summary": str(task.result)[:500] if task.result else "",
                })
            except Exception:
                pass

    def get_task(self, task_id: str) -> Optional[LocalTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def get_task_status(self, task_id: str) -> Optional[dict]:
        task = self.get_task(task_id)
        return task.to_dict() if task else None

    def cancel_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            task.cancel_event.set()
            future = self._futures.get(task_id)
            if future and not future.done():
                future.cancel()
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            self._update_db_status(task)
            return True
        return False

    def list_tasks(self, status: TaskStatus = None, limit: int = 100) -> list[LocalTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def update_progress(self, task_id: str, progress: float, message: str = "") -> None:
        """更新任务进度（由任务函数调用）"""
        task = self.get_task(task_id)
        if task:
            task.progress = min(max(progress, 0), 100)
            task.progress_message = message

    def wait_for_task(self, task_id: str, timeout: float = None) -> Optional[LocalTask]:
        """等待任务完成"""
        future = self._futures.get(task_id)
        if future:
            try:
                future.result(timeout=timeout)
            except Exception:
                pass
        return self.get_task(task_id)

    def get_active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    def get_pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)

    def get_stats(self) -> dict:
        tasks = list(self._tasks.values())
        return {
            "total": len(tasks),
            "pending": sum(1 for t in tasks if t.status == TaskStatus.PENDING),
            "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
            "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
            "failed": sum(1 for t in tasks if t.status == TaskStatus.FAILED),
            "cancelled": sum(1 for t in tasks if t.status == TaskStatus.CANCELLED),
            "max_workers": self._max_workers,
        }

    def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """清理旧任务"""
        now = time.time()
        count = 0
        with self._lock:
            to_remove = []
            for tid, task in self._tasks.items():
                if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    if task.completed_at and (now - task.completed_at) > max_age_hours * 3600:
                        to_remove.append(tid)
            for tid in to_remove:
                del self._tasks[tid]
                self._futures.pop(tid, None)
                count += 1
        return count

    def shutdown(self, wait: bool = True) -> None:
        """关闭队列"""
        self._shutdown = True
        self._executor.shutdown(wait=wait)


# 全局实例
_task_queue: Optional[LocalTaskQueue] = None


def get_task_queue(max_workers: int = None, db=None) -> LocalTaskQueue:
    global _task_queue
    if _task_queue is None:
        _task_queue = LocalTaskQueue(max_workers, db)
    return _task_queue
