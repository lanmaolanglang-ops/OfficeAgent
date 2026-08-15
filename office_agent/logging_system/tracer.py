"""
全链路追踪

记录 API → Task Queue → Workflow → Agent → Tool → Model → Storage 的完整调用链。
"""
import time
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from .context import (
    get_trace_id, set_trace_id, get_request_id, get_task_id,
    generate_span_id, get_context_dict,
)
from .logger import get_logger

logger = get_logger("trace")


class Span:
    """追踪 Span"""

    def __init__(self, name: str, span_type: str = "operation",
                 span_id: str = None, parent_id: str = None,
                 trace_id: str = None, attributes: dict = None):
        self.name = name
        self.span_type = span_type  # api/task/workflow/agent/tool/model/storage
        self.span_id = span_id or generate_span_id()
        self.parent_id = parent_id
        self.trace_id = trace_id or get_trace_id()
        self.attributes = attributes or {}
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.status = "ok"
        self.error = None
        self.events: List[dict] = []
        self.children: List["Span"] = []

    def set_attribute(self, key: str, value: Any):
        self.attributes[key] = value

    def add_event(self, name: str, **attributes):
        self.events.append({
            "name": name,
            "time": time.time(),
            "attributes": attributes,
        })

    def set_error(self, error: Exception):
        self.status = "error"
        self.error = str(error)
        self.set_attribute("error.type", type(error).__name__)
        self.set_attribute("error.message", str(error))

    def end(self):
        self.end_time = time.time()

    @property
    def duration_ms(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return (time.time() - self.start_time) * 1000

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.span_type,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "trace_id": self.trace_id,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "end_time": datetime.fromtimestamp(self.end_time).isoformat() if self.end_time else None,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "error": self.error,
            "attributes": self.attributes,
            "events": self.events,
            "children": [c.to_dict() for c in self.children],
        }


class TraceContext:
    """追踪上下文（线程内）"""

    def __init__(self):
        self.spans: Dict[str, Span] = {}
        self.root: Optional[Span] = None
        self._current_stack: List[str] = []

    def start_span(self, name: str, span_type: str = "operation",
                   attributes: dict = None) -> Span:
        parent_id = self._current_stack[-1] if self._current_stack else None
        span = Span(
            name=name,
            span_type=span_type,
            parent_id=parent_id,
            attributes=attributes,
        )
        self.spans[span.span_id] = span

        if parent_id and parent_id in self.spans:
            self.spans[parent_id].children.append(span)
        elif not self.root:
            self.root = span

        self._current_stack.append(span.span_id)

        logger.debug(
            f"Span start: {name} ({span_type})",
            extra={
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "parent_id": span.parent_id,
                "span_type": span_type,
            },
        )
        return span

    def end_span(self, span: Span, error: Exception = None):
        if error:
            span.set_error(error)
        span.end()

        if span.span_id in self._current_stack:
            self._current_stack.remove(span.span_id)

        log_level = logger.error if error else logger.debug
        log_level(
            f"Span end: {span.name} ({span.duration_ms:.1f}ms) {span.status}",
            extra={
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "duration_ms": span.duration_ms,
                "status": span.status,
            },
        )

    def get_current_span(self) -> Optional[Span]:
        if self._current_stack:
            return self.spans.get(self._current_stack[-1])
        return None

    def to_dict(self) -> dict:
        if not self.root:
            return {"trace_id": get_trace_id(), "spans": []}
        return {
            "trace_id": self.root.trace_id,
            "request_id": get_request_id(),
            "task_id": get_task_id(),
            "root": self.root.to_dict(),
            "total_duration_ms": self.root.duration_ms,
            "span_count": len(self.spans),
        }


# 线程本地存储
import threading
_local = threading.local()


def get_trace_context() -> TraceContext:
    if not hasattr(_local, "trace_context"):
        _local.trace_context = TraceContext()
    return _local.trace_context


@contextmanager
def trace_span(name: str, span_type: str = "operation", **attributes):
    """
    追踪 Span 上下文管理器

    用法：
        with trace_span("format_document", "agent", agent="WordAgent") as span:
            do_something()
    """
    ctx = get_trace_context()
    span = ctx.start_span(name, span_type, attributes)
    try:
        yield span
        ctx.end_span(span)
    except Exception as e:
        ctx.end_span(span, error=e)
        raise


@contextmanager
def trace_api(method: str, path: str):
    """追踪 API 请求"""
    with trace_span(f"{method} {path}", "api", method=method, path=path) as span:
        yield span


@contextmanager
def trace_task(task_id: str, task_type: str):
    """追踪任务执行"""
    with trace_span(f"task:{task_type}", "task",
                    task_id=task_id, task_type=task_type) as span:
        yield span


@contextmanager
def trace_agent(agent_name: str, action: str):
    """追踪 Agent 执行"""
    with trace_span(f"{agent_name}.{action}", "agent",
                    agent=agent_name, action=action) as span:
        yield span


@contextmanager
def trace_model(model_name: str, provider: str = None):
    """追踪模型调用"""
    with trace_span(f"model:{model_name}", "model",
                    model=model_name, provider=provider) as span:
        yield span


@contextmanager
def trace_tool(tool_name: str):
    """追踪工具调用"""
    with trace_span(f"tool:{tool_name}", "tool", tool=tool_name) as span:
        yield span


@contextmanager
def trace_storage(operation: str):
    """追踪存储操作"""
    with trace_span(f"storage:{operation}", "storage", operation=operation) as span:
        yield span


def get_trace_tree() -> dict:
    """获取当前追踪树"""
    return get_trace_context().to_dict()
