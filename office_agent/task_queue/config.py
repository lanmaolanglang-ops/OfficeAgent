"""
任务队列配置（本地线程池模式，零外部依赖）
"""
import os
from pathlib import Path


class QueueConfig:
    """任务队列配置"""

    # 注：线程池模型无法强杀运行中的线程，任务级整体超时/重试未实现，
    # 故不再保留误导性的超时/重试参数；模型级故障转移由 model_gateway 的 fallback 链承担。

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
