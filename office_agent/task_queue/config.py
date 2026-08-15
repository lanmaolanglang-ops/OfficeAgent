"""
任务队列配置

支持两种模式：
1. celery  - 使用 Redis + Celery（生产环境）
2. local   - 本地线程池（开发/零依赖，默认）

通过环境变量切换：
    TASK_QUEUE_MODE=celery
    REDIS_URL=redis://localhost:6379/0
"""
import os
from pathlib import Path


class QueueConfig:
    """任务队列配置"""

    # 运行模式: local / celery
    MODE: str = os.environ.get("TASK_QUEUE_MODE", "local")

    # Redis 配置
    REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    REDIS_BROKER_DB: int = int(os.environ.get("REDIS_BROKER_DB", "0"))
    REDIS_BACKEND_DB: int = int(os.environ.get("REDIS_BACKEND_DB", "1"))

    @property
    def broker_url(self) -> str:
        if self.REDIS_URL.endswith("/"):
            return f"{self.REDIS_URL}{self.REDIS_BROKER_DB}"
        return f"{self.REDIS_URL}/{self.REDIS_BROKER_DB}"

    @property
    def result_backend(self) -> str:
        if self.REDIS_URL.endswith("/"):
            return f"{self.REDIS_URL}{self.REDIS_BACKEND_DB}"
        return f"{self.REDIS_URL}/{self.REDIS_BACKEND_DB}"

    # Worker 配置
    WORKER_CONCURRENCY: int = int(os.environ.get("WORKER_CONCURRENCY", "4"))
    WORKER_MAX_TASKS_PER_CHILD: int = int(os.environ.get("WORKER_MAX_TASKS", "100"))
    WORKER_PREFETCH_MULTIPLIER: int = int(os.environ.get("WORKER_PREFETCH", "1"))

    # 任务配置
    TASK_SERIALIZER: str = "json"
    RESULT_SERIALIZER: str = "json"
    ACCEPT_CONTENT: list = ["json"]
    TIMEZONE: str = "Asia/Shanghai"
    ENABLE_UTC: bool = False

    # 任务超时（秒）
    TASK_SOFT_TIMEOUT: int = int(os.environ.get("TASK_SOFT_TIMEOUT", "300"))
    TASK_HARD_TIMEOUT: int = int(os.environ.get("TASK_HARD_TIMEOUT", "600"))

    # 重试配置
    TASK_MAX_RETRIES: int = int(os.environ.get("TASK_MAX_RETRIES", "3"))
    TASK_RETRY_DELAY: int = int(os.environ.get("TASK_RETRY_DELAY", "5"))
    TASK_RETRY_BACKOFF: bool = True

    # 优先级队列
    TASK_QUEUES = {
        "high": {"priority": 9, "concurrency": 2},
        "normal": {"priority": 5, "concurrency": 4},
        "low": {"priority": 1, "concurrency": 2},
    }

    # 本地线程池配置
    LOCAL_MAX_WORKERS: int = int(os.environ.get("LOCAL_MAX_WORKERS", "4"))

    # 结果过期时间（秒）
    RESULT_EXPIRES: int = 86400  # 24小时

    # 数据目录
    DATA_DIR = Path(os.path.expanduser("~/.office_agent"))
    LOG_DIR = DATA_DIR / "logs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)


# 全局配置实例
config = QueueConfig()
