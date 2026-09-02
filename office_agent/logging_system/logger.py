"""
统一日志入口

用法：
    from office_agent.logging_system import get_logger
    logger = get_logger("word_agent")
    logger.info("processing document", extra={"file_id": "xxx"})
"""
import os
import logging
import threading
import time
from typing import Optional

from .handlers import (
    create_console_handler, create_file_handler,
    create_error_file_handler, DatabaseLogHandler, ModelCallLogHandler,
)
from .context import get_context_dict

# 根 logger 名称
ROOT_LOGGER_NAME = "office_agent"

_initialized = False
_setup_lock = threading.RLock()


def _synchronized(lock):
    def decorate(func):
        def wrapped(*args, **kwargs):
            with lock:
                return func(*args, **kwargs)
        return wrapped
    return decorate


@_synchronized(_setup_lock)
def setup_logging(
    log_level: str = None,
    log_dir: str = None,
    enable_db_logging: bool = True,
    enable_file_logging: bool = True,
    service_name: str = "office-agent",
) -> logging.Logger:
    """
    初始化日志系统

    Args:
        log_level: 日志级别 DEBUG/INFO/WARNING/ERROR
        log_dir: 日志文件目录
        enable_db_logging: 是否启用数据库日志
        enable_file_logging: 是否启用文件日志
    """
    global _initialized

    level_name = log_level or os.environ.get("LOG_LEVEL", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)

    if log_dir is None:
        log_dir = (os.environ.get("LOG_DIR") or os.environ.get("OFFICE_AGENT_LOG_DIR")
                  or os.path.expanduser("~/.office_agent/logs"))

    # 根 logger
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level)

    # 清除已有 handler（避免重复）
    old_handlers = list(root.handlers)
    root.handlers.clear()
    for handler in old_handlers:
        try:
            handler.close()
        except Exception:
            pass

    # 控制台
    console_level = logging.DEBUG if level == logging.DEBUG else logging.INFO
    root.addHandler(create_console_handler(console_level))

    # 文件
    if enable_file_logging:
        root.addHandler(create_file_handler(log_dir, level=level))
        root.addHandler(create_error_file_handler(log_dir))

    # 数据库
    if enable_db_logging:
        try:
            root.addHandler(DatabaseLogHandler(level=logging.ERROR))
            root.addHandler(ModelCallLogHandler())
        except Exception as e:
            root.warning(f"数据库日志初始化失败: {e}")

    # 防止传播到 root logger
    root.propagate = False

    _initialized = True
    root.info(f"日志系统初始化: level={level_name}, dir={log_dir}, db={enable_db_logging}")
    return root


def get_logger(name: str = None) -> logging.Logger:
    """
    获取 logger

    Args:
        name: logger 名称，自动加 office_agent 前缀
    """
    if not _initialized:
        setup_logging()

    if name:
        if not name.startswith(ROOT_LOGGER_NAME):
            name = f"{ROOT_LOGGER_NAME}.{name}"
    else:
        name = ROOT_LOGGER_NAME

    return logging.getLogger(name)


# 便捷函数
def log_agent_execution(agent_name: str, action: str, task_id: str = None,
                        input_summary: str = None, output_summary: str = None,
                        duration_ms: float = None, status: str = "success",
                        **extra):
    """记录 Agent 执行日志"""
    logger = get_logger(f"agent.{agent_name.lower()}")
    extra_data = {
        "agent_name": agent_name,
        "action": action,
        "status": status,
    }
    if input_summary:
        extra_data["input_summary"] = input_summary[:500]
    if output_summary:
        extra_data["output_summary"] = output_summary[:500]
    if duration_ms is not None:
        extra_data["duration_ms"] = duration_ms
    extra_data.update(extra)

    level = logging.INFO if status == "success" else logging.ERROR
    logger.log(level, f"[{agent_name}] {action} {status}", extra=extra_data)


def log_model_call(model_name: str, provider: str = None,
                   input_tokens: int = 0, output_tokens: int = 0,
                   latency_ms: float = 0, cost_estimate: float = 0.0,
                   status: str = "success", error_message: str = "",
                   **extra):
    """
    记录模型调用

    通过特殊 logger "model.{provider}" 触发 ModelCallLogHandler 写入数据库。
    """
    logger = get_logger(f"model.{provider or 'unknown'}")
    model_data = {
        "model_name": model_name,
        "provider": provider,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "latency_ms": latency_ms,
        "cost_estimate": cost_estimate,
        "status": status,
        "error_message": error_message,
        "extra": extra,
    }
    logger.info(
        f"[{provider or 'model'}] {model_name} "
        f"tokens={input_tokens}+{output_tokens} "
        f"latency={latency_ms:.0f}ms cost=${cost_estimate:.4f} {status}",
        extra={"model_data": model_data},
    )


def log_task_event(task_id: str, event: str, status: str = "running",
                   details: dict = None):
    """记录任务事件"""
    logger = get_logger("task")
    logger.info(
        f"[Task] {task_id} {event} {status}",
        extra={
            "task_id": task_id,
            "event": event,
            "status": status,
            "details": details or {},
        },
    )


class TimedOperation:
    """
    计时操作上下文管理器

    用法：
        with TimedOperation("word_format", logger) as t:
            do_something()
        # 自动记录耗时
    """

    def __init__(self, operation: str, logger: logging.Logger = None,
                 level: int = logging.INFO, **extra):
        self.operation = operation
        self.logger = logger or get_logger("operation")
        self.level = level
        self.extra = extra
        self.start = 0
        self.duration_ms = 0

    def __enter__(self):
        self.start = time.time()
        self.logger.log(self.level, f"开始: {self.operation}", extra=self.extra)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.duration_ms = (time.time() - self.start) * 1000
        if exc_type:
            self.logger.error(
                f"失败: {self.operation} ({self.duration_ms:.0f}ms) - {exc_val}",
                exc_info=True,
                extra={"duration_ms": self.duration_ms, **self.extra},
            )
        else:
            self.logger.log(
                self.level,
                f"完成: {self.operation} ({self.duration_ms:.0f}ms)",
                extra={"duration_ms": self.duration_ms, **self.extra},
            )
        return False
