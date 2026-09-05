"""
JSON 日志格式化器

所有日志输出为结构化 JSON，便于 ELK/Grafana/Loki 采集。
"""
import json
import logging
import traceback
from datetime import datetime, timezone

from .context import get_context_dict


class JsonFormatter(logging.Formatter):
    """
    JSON 结构化日志格式化器

    输出格式：
    {
        "time": "2026-07-31T20:00:00.000Z",
        "level": "INFO",
        "service": "office-agent",
        "logger": "word_agent",
        "request_id": "req_xxx",
        "task_id": "task_xxx",
        "agent": "WordAgent",
        "message": "...",
        "duration_ms": 1234,
        "extra": {...}
    }
    """

    def __init__(self, service_name: str = "office-agent",
                 include_extra: bool = True):
        super().__init__()
        self.service_name = service_name
        self.include_extra = include_extra

    def format(self, record: logging.LogRecord) -> str:
        # 基础字段
        log_entry: dict = {
            "time": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
        }

        # 上下文
        ctx = get_context_dict()
        for k, v in ctx.items():
            if v:
                log_entry[k] = v

        # 消息
        log_entry["message"] = record.getMessage()

        # 耗时
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms

        # 额外字段
        if self.include_extra:
            extra_fields = {}
            for key, value in record.__dict__.items():
                if key not in (
                    "name", "msg", "args", "levelname", "levelno", "pathname",
                    "filename", "module", "exc_info", "exc_text", "stack_info",
                    "lineno", "funcName", "created", "msecs", "relativeCreated",
                    "thread", "threadName", "processName", "process",
                    "message", "duration_ms", "taskName",
                ):
                    extra_fields[key] = value
            if extra_fields:
                log_entry["extra"] = extra_fields

        # 异常信息
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "",
                "message": str(record.exc_info[1]) if record.exc_info[1] else "",
                "traceback": traceback.format_exception(*record.exc_info),
            }

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class HumanReadableFormatter(logging.Formatter):
    """
    开发环境可读格式

    2026-07-31 20:00:00 | INFO | WordAgent | [req_xxx] message
    """

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        time_str = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        level = record.levelname
        ctx = get_context_dict()
        rid = ctx.get("request_id", "")
        agent = ctx.get("agent", "")
        task = ctx.get("task_id", "")

        prefix_parts = [time_str, level]
        if agent:
            prefix_parts.append(agent)
        if rid:
            prefix_parts.append(f"[{rid[:20]}]")
        if task:
            prefix_parts.append(f"task={task[:16]}")

        prefix = " | ".join(prefix_parts)
        msg = record.getMessage()

        duration = ""
        if hasattr(record, "duration_ms"):
            duration = f" ({record.duration_ms:.0f}ms)"

        line = f"{prefix} {msg}{duration}"

        if self.use_color and level in self.COLORS:
            line = f"{self.COLORS[level]}{line}{self.RESET}"

        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info))

        return line
