"""
请求上下文管理

使用 contextvars 在异步/多线程环境中传递 request_id、task_id 等上下文。
"""
import contextvars
import uuid

# 请求级上下文
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
_task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_id", default=""
)
_agent_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agent_name", default=""
)
_user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_id", default=""
)
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)


def generate_request_id() -> str:
    """生成请求ID"""
    return f"req_{uuid.uuid4().hex[:16]}"


def generate_trace_id() -> str:
    """生成追踪ID"""
    return f"trace_{uuid.uuid4().hex[:16]}"


def generate_span_id() -> str:
    """生成Span ID"""
    return f"span_{uuid.uuid4().hex[:12]}"


def set_request_id(request_id: str | None = None) -> contextvars.Token:
    """设置当前请求ID"""
    rid = request_id or generate_request_id()
    return _request_id_var.set(rid)


def get_request_id() -> str:
    """获取当前请求ID"""
    return _request_id_var.get()


def set_task_id(task_id: str) -> contextvars.Token:
    return _task_id_var.set(task_id)


def get_task_id() -> str:
    return _task_id_var.get()


def set_agent_name(agent_name: str) -> contextvars.Token:
    return _agent_var.set(agent_name)


def get_agent_name() -> str:
    return _agent_var.get()


def set_user_id(user_id: str) -> contextvars.Token:
    return _user_id_var.set(user_id)


def get_user_id() -> str:
    return _user_id_var.get()


def set_trace_id(trace_id: str | None = None) -> contextvars.Token:
    tid = trace_id or generate_trace_id()
    return _trace_id_var.set(tid)


def get_trace_id() -> str:
    return _trace_id_var.get()


def get_context_dict() -> dict:
    """获取当前上下文字典（用于日志）"""
    return {
        "request_id": _request_id_var.get(),
        "task_id": _task_id_var.get(),
        "agent": _agent_var.get(),
        "user_id": _user_id_var.get(),
        "trace_id": _trace_id_var.get(),
    }


class LogContext:
    """
    上下文管理器，用于在代码块中设置日志上下文

    用法：
        with LogContext(task_id="xxx", agent_name="WordAgent"):
            logger.info("processing...")
    """

    def __init__(self, request_id: str | None = None, task_id: str | None = None,
                 agent_name: str | None = None, user_id: str | None = None,
                 trace_id: str | None = None):
        self.request_id = request_id
        self.task_id = task_id
        self.agent_name = agent_name
        self.user_id = user_id
        self.trace_id = trace_id
        self._tokens: list[tuple[str, contextvars.Token]] = []

    def __enter__(self):
        if self.request_id:
            self._tokens.append(("request_id", _request_id_var.set(self.request_id)))
        if self.task_id:
            self._tokens.append(("task_id", _task_id_var.set(self.task_id)))
        if self.agent_name:
            self._tokens.append(("agent", _agent_var.set(self.agent_name)))
        if self.user_id:
            self._tokens.append(("user_id", _user_id_var.set(self.user_id)))
        if self.trace_id:
            self._tokens.append(("trace_id", _trace_id_var.set(self.trace_id)))
        return self

    def __exit__(self, *args):
        var_map = {
            "request_id": _request_id_var,
            "task_id": _task_id_var,
            "agent": _agent_var,
            "user_id": _user_id_var,
            "trace_id": _trace_id_var,
        }
        for name, token in reversed(self._tokens):
            var_map[name].reset(token)
