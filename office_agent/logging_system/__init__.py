"""
Logging & Monitoring Layer

统一日志、监控、追踪系统。
"""
from .context import (
    LogContext,
    get_request_id, set_request_id, generate_request_id,
    get_task_id, set_task_id,
    get_agent_name, set_agent_name,
    get_user_id, set_user_id,
    get_trace_id, set_trace_id, generate_trace_id,
    generate_span_id,
    get_context_dict,
)
from .logger import (
    setup_logging, get_logger,
    log_agent_execution, log_model_call, log_task_event,
    TimedOperation,
)
from .formatter import JsonFormatter, HumanReadableFormatter
from .handlers import (
    create_console_handler, create_file_handler,
    create_error_file_handler, DatabaseLogHandler, ModelCallLogHandler,
)
from .middleware import RequestLoggingMiddleware, ErrorHandlingMiddleware
from .metrics import (
    registry, Counter, Gauge, Histogram, Timer,
    init_default_metrics,
)
from .tracer import (
    Span, TraceContext,
    trace_span, trace_api, trace_task, trace_agent,
    trace_model, trace_tool, trace_storage,
    get_trace_tree, get_trace_context,
)
from .decorators import log_execution, log_model_call_decorator
from .._version import __version__

__all__ = [
    "__version__",
    # Context
    "LogContext",
    "get_request_id", "set_request_id", "generate_request_id",
    "get_task_id", "set_task_id",
    "get_agent_name", "set_agent_name",
    "get_user_id", "set_user_id",
    "get_trace_id", "set_trace_id", "generate_trace_id",
    "generate_span_id",
    "get_context_dict",
    # Logger
    "setup_logging", "get_logger",
    "log_agent_execution", "log_model_call", "log_task_event",
    "TimedOperation",
    # Formatter
    "JsonFormatter", "HumanReadableFormatter",
    # Handlers
    "create_console_handler", "create_file_handler",
    "create_error_file_handler", "DatabaseLogHandler", "ModelCallLogHandler",
    # Middleware
    "RequestLoggingMiddleware", "ErrorHandlingMiddleware",
    # Metrics
    "registry", "Counter", "Gauge", "Histogram", "Timer",
    "init_default_metrics",
    # Tracer
    "Span", "TraceContext",
    "trace_span", "trace_api", "trace_task", "trace_agent",
    "trace_model", "trace_tool", "trace_storage",
    "get_trace_tree", "get_trace_context",
    # Decorators
    "log_execution", "log_model_call_decorator",
]
