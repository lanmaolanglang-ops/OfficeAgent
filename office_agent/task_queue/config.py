"""
任务队列配置（本地线程池模式，零外部依赖）
"""
import os
from pathlib import Path


class QueueConfig:
    """任务队列配置"""

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
