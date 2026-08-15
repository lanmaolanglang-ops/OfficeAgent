"""
Celery 应用配置

启动 Worker:
    celery -A office_agent.task_queue.celery_app worker --loglevel=info --pool=solo -Q high,normal,low

启动 Beat（定时任务）:
    celery -A office_agent.task_queue.celery_app beat --loglevel=info

注意：Windows 环境需要 --pool=solo 或安装 gevent/eventlet
"""
import os
import sys

# 确保项目路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from .config import config

# 尝试导入 Celery
try:
    from celery import Celery
    CELERY_AVAILABLE = True
except ImportError:
    CELERY_AVAILABLE = False
    Celery = None


def create_celery_app():
    """创建 Celery 应用"""
    if not CELERY_AVAILABLE:
        return None

    app = Celery(
        "office_agent",
        broker=config.broker_url,
        backend=config.result_backend,
    )

    # 基础配置
    app.conf.update(
        task_serializer=config.TASK_SERIALIZER,
        result_serializer=config.RESULT_SERIALIZER,
        accept_content=config.ACCEPT_CONTENT,
        timezone=config.TIMEZONE,
        enable_utc=config.ENABLE_UTC,

        # 任务执行
        task_soft_time_limit=config.TASK_SOFT_TIMEOUT,
        task_time_limit=config.TASK_HARD_TIMEOUT,
        task_acks_late=True,
        worker_prefetch_multiplier=config.WORKER_PREFETCH_MULTIPLIER,
        worker_max_tasks_per_child=config.WORKER_MAX_TASKS_PER_CHILD,

        # 结果
        result_expires=config.RESULT_EXPIRES,

        # 队列
        task_queues={
            "high": {"exchange": "high", "routing_key": "high.#"},
            "normal": {"exchange": "normal", "routing_key": "normal.#"},
            "low": {"exchange": "low", "routing_key": "low.#"},
        },
        task_default_queue="normal",
        task_default_exchange="normal",
        task_default_routing_key="normal.default",

        # 重试
        task_publish_retry=True,
        task_publish_retry_policy={
            "max_retries": config.TASK_MAX_RETRIES,
            "interval_start": config.TASK_RETRY_DELAY,
            "interval_step": 5,
            "interval_max": 60,
        },
    )

    # 自动发现任务
    app.autodiscover_tasks([
        "office_agent.task_queue.tasks",
    ])

    # 定时任务（Celery Beat）
    app.conf.beat_schedule = {
        "cleanup-temp-files-daily": {
            "task": "office_agent.task_queue.tasks.file_tasks.cleanup_temp_files",
            "schedule": 86400.0,  # 每天
            "options": {"queue": "low"},
        },
        "update-knowledge-weekly": {
            "task": "office_agent.task_queue.tasks.rag_tasks.refresh_knowledge_base",
            "schedule": 604800.0,  # 每周
            "options": {"queue": "low"},
        },
        "system-health-check": {
            "task": "office_agent.task_queue.tasks.file_tasks.system_health_check",
            "schedule": 3600.0,  # 每小时
            "options": {"queue": "low"},
        },
    }

    return app


# 全局 Celery 应用
celery_app = create_celery_app() if CELERY_AVAILABLE else None
