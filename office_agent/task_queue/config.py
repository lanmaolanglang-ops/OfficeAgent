"""
任务队列配置（本地线程池模式，零外部依赖）
"""
import os
from ..runtime_config import get_data_root


class QueueConfig:
    """任务队列配置"""

    # 软超时（秒）：任务运行超过该时长则标记为失败（不杀线程，线程自然结束）。
    # 线程池模型无法强杀运行中的线程，故仅做软超时；模型级故障转移由 gateway 承担。
    # 默认 30 分钟：PPT 规划 + 多张生图的链路常超过 5 分钟，误杀会把进行中的任务标失败。
    TASK_SOFT_TIMEOUT: int = int(os.environ.get("TASK_SOFT_TIMEOUT", "1800"))

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
    DATA_DIR = get_data_root()
    LOG_DIR = DATA_DIR / "logs"


# 全局配置实例
config = QueueConfig()
