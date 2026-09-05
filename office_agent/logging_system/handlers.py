"""
日志处理器

- 控制台输出（开发环境可读格式）
- 文件轮转（JSON格式，按大小/时间轮转）
- 数据库写入（ERROR及以上，或指定logger）
"""
import os
import logging
import logging.handlers
import threading

from .formatter import JsonFormatter, HumanReadableFormatter
from .background import submit as submit_background


def create_console_handler(level: int = logging.DEBUG,
                           use_color: bool = True) -> logging.Handler:
    """创建控制台处理器"""
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(HumanReadableFormatter(use_color=use_color))
    return handler


def create_file_handler(log_dir: str, filename: str = "office_agent.log",
                        level: int = logging.INFO,
                        max_bytes: int = 50 * 1024 * 1024,
                        backup_count: int = 10) -> logging.Handler:
    """
    创建文件轮转处理器（JSON格式）

    Args:
        log_dir: 日志目录
        filename: 日志文件名
        max_bytes: 单文件最大大小（默认50MB）
        backup_count: 保留文件数
    """
    os.makedirs(log_dir, exist_ok=True)
    filepath = os.path.join(log_dir, filename)

    handler = logging.handlers.RotatingFileHandler(
        filepath,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    return handler


def create_error_file_handler(log_dir: str,
                              level: int = logging.ERROR) -> logging.Handler:
    """创建错误日志文件处理器"""
    os.makedirs(log_dir, exist_ok=True)
    filepath = os.path.join(log_dir, "error.log")

    handler = logging.handlers.RotatingFileHandler(
        filepath,
        maxBytes=50 * 1024 * 1024,
        backupCount=20,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    return handler


class DatabaseLogHandler(logging.Handler):
    """
    数据库日志处理器

    将 ERROR 及以上日志异步写入数据库 ErrorLog 表。
    使用线程池避免阻塞主流程。
    """

    def __init__(self, level: int = logging.ERROR,
                 async_write: bool = True):
        super().__init__(level)
        self.async_write = async_write
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            if getattr(record, "skip_db_log", False):
                return
            from .context import get_context_dict
            record.office_agent_context = get_context_dict()
            if self.async_write:
                if not submit_background(self._write_to_db, record):
                    self.handleError(record)
            else:
                self._write_to_db(record)
        except Exception:
            self.handleError(record)

    def _write_to_db(self, record: logging.LogRecord):
        """写入数据库"""
        try:
            from ..database.session import SessionLocal
            from ..database.models import ErrorLog
            import traceback
            import json

            session = SessionLocal()
            try:
                ctx = getattr(record, "office_agent_context", {})

                stack_trace = None
                if record.exc_info:
                    stack_trace = "".join(
                        traceback.format_exception(*record.exc_info)
                    )

                exc_type = record.exc_info[0] if record.exc_info else None
                error_log = ErrorLog(
                    request_id=ctx.get("request_id", ""),
                    task_id=ctx.get("task_id", ""),
                    logger_name=record.name,
                    level=record.levelname,
                    error_type=exc_type.__name__ if exc_type else record.levelname,
                    error_message=record.getMessage()[:2000],
                    stack_trace=stack_trace,
                    extra_json=json.dumps({
                        "pathname": record.pathname,
                        "lineno": record.lineno,
                        "funcName": record.funcName,
                        "agent": ctx.get("agent", ""),
                        "user_id": ctx.get("user_id", ""),
                    }, ensure_ascii=False),
                )
                session.add(error_log)
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()
        except Exception:
            pass


class ModelCallLogHandler(logging.Handler):
    """
    模型调用日志处理器

    捕获 logger 以 "office_agent.model." 开头的日志，写入 ModelCallLog 表。
    """

    def __init__(self):
        super().__init__(logging.INFO)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        if not record.name.startswith("office_agent.model."):
            return
        try:
            from .context import get_context_dict
            record.office_agent_context = get_context_dict()
            submit_background(self._write_to_db, record)
        except Exception:
            pass

    def _write_to_db(self, record: logging.LogRecord):
        try:
            from ..database.session import SessionLocal
            from ..database.models import ModelCallLog
            import json

            session = SessionLocal()
            try:
                ctx = getattr(record, "office_agent_context", {})

                data = getattr(record, "model_data", None)
                if not data:
                    return

                call_log = ModelCallLog(
                    request_id=ctx.get("request_id", ""),
                    task_id=ctx.get("task_id", ""),
                    model_name=data.get("model_name", record.name),
                    provider=data.get("provider", ""),
                    input_tokens=data.get("input_tokens", 0),
                    output_tokens=data.get("output_tokens", 0),
                    total_tokens=data.get("total_tokens", 0),
                    latency_ms=data.get("latency_ms", 0),
                    cost_estimate=data.get("cost_estimate", 0.0),
                    status=data.get("status", "success"),
                    error_message=data.get("error_message", ""),
                    extra_json=json.dumps(data.get("extra", {}), ensure_ascii=False),
                )
                session.add(call_log)
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()
        except Exception:
            pass
