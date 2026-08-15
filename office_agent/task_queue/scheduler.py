"""
定时任务调度器

本地模式：使用线程定时器（不依赖 Celery Beat）
Celery 模式：使用 Celery Beat（在 celery_app.py 中配置）

内置定时任务：
- 每小时：系统健康检查
- 每天：清理临时文件
- 每周：刷新知识库
"""
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

logger = logging.getLogger("office_agent.scheduler")


class ScheduledTask:
    """定时任务"""

    def __init__(self, name: str, func: Callable, interval_seconds: int,
                 priority: str = "low", args: tuple = (), kwargs: dict = None):
        self.name = name
        self.func = func
        self.interval = interval_seconds
        self.priority = priority
        self.args = args
        self.kwargs = kwargs or {}
        self.last_run: Optional[datetime] = None
        self.next_run: datetime = datetime.now() + timedelta(seconds=interval_seconds)
        self.run_count = 0
        self.enabled = True


class TaskScheduler:
    """
    轻量级定时任务调度器

    用法：
        scheduler = TaskScheduler()
        scheduler.add("cleanup", cleanup_func, interval=86400)
        scheduler.start()
    """

    def __init__(self):
        self._tasks: Dict[str, ScheduledTask] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    def add(self, name: str, func: Callable, interval_seconds: int,
            priority: str = "low", args: tuple = (), kwargs: dict = None):
        """添加定时任务"""
        task = ScheduledTask(name, func, interval_seconds, priority, args, kwargs)
        with self._lock:
            self._tasks[name] = task
        logger.info(f"定时任务已添加: {name} (每 {interval_seconds}s)")

    def remove(self, name: str):
        """移除定时任务"""
        with self._lock:
            self._tasks.pop(name, None)

    def start(self):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="scheduler")
        self._thread.start()
        logger.info("定时任务调度器已启动")

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("定时任务调度器已停止")

    def _run_loop(self):
        """调度主循环"""
        while self._running:
            now = datetime.now()
            tasks_to_run = []

            with self._lock:
                for task in self._tasks.values():
                    if task.enabled and now >= task.next_run:
                        tasks_to_run.append(task)

            for task in tasks_to_run:
                self._execute(task)

            time.sleep(1)  # 每秒检查一次

    def _execute(self, task: ScheduledTask):
        """执行定时任务"""
        task.last_run = datetime.now()
        task.next_run = task.last_run + timedelta(seconds=task.interval)
        task.run_count += 1

        def _do_run():
            try:
                logger.info(f"执行定时任务: {task.name}")
                task.func(*task.args, **task.kwargs)
                logger.info(f"定时任务完成: {task.name}")
            except Exception as e:
                logger.error(f"定时任务 {task.name} 失败: {e}")

        # 提交到任务队列执行
        try:
            from . import submit_task, init_worker
            init_worker()
            # 直接在线程中执行（定时任务通常不紧急）
            threading.Thread(target=_do_run, daemon=True).start()
        except Exception as e:
            logger.warning(f"提交定时任务到队列失败，直接执行: {e}")
            _do_run()

    def list_tasks(self):
        """列出所有定时任务"""
        with self._lock:
            return [
                {
                    "name": t.name,
                    "interval": t.interval,
                    "priority": t.priority,
                    "last_run": t.last_run.isoformat() if t.last_run else None,
                    "next_run": t.next_run.isoformat(),
                    "run_count": t.run_count,
                    "enabled": t.enabled,
                }
                for t in self._tasks.values()
            ]


# 全局调度器
scheduler = TaskScheduler()


def setup_default_schedules(sched: TaskScheduler = None):
    """设置默认定时任务"""
    from .tasks.file_tasks import cleanup_temp_files, system_health_check
    from .tasks.rag_tasks import refresh_knowledge_base

    sched = sched or scheduler

    sched.add(
        "system_health_check",
        system_health_check,
        interval_seconds=3600,  # 每小时
        priority="low",
    )
    sched.add(
        "cleanup_temp_files",
        cleanup_temp_files,
        interval_seconds=86400,  # 每天
        priority="low",
    )
    sched.add(
        "refresh_knowledge_base",
        refresh_knowledge_base,
        interval_seconds=604800,  # 每周
        priority="low",
    )


def start_scheduler():
    """启动调度器（在 API 启动时调用）"""
    setup_default_schedules()
    scheduler.start()
    return scheduler
