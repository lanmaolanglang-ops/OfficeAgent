"""
日志装饰器

提供 Agent 执行日志、模型调用日志的装饰器，不侵入业务代码。

用法：
    from office_agent.logging_system.decorators import log_execution

    class WordAgent:
        @log_execution(action="format_document")
        def format_document(self, content, **kwargs):
            ...
"""
import time
import functools
from typing import Callable, Optional

from .logger import get_logger, log_agent_execution
from .context import (
    set_agent_name, set_task_id, get_task_id,
    get_request_id, get_agent_name, LogContext,
)
from .metrics import registry
from .tracer import trace_agent


def log_execution(agent_name: str = None, action: str = None,
                  log_input: bool = True, log_output: bool = True,
                  input_max_len: int = 500, output_max_len: int = 500):
    """
    Agent 执行日志装饰器

    自动记录：
    - 开始/结束时间
    - 耗时
    - 输入/输出摘要
    - 成功/失败状态
    - 指标计数
    - 追踪 span

    Args:
        agent_name: Agent 名称（默认从 self.name 获取）
        action: 动作名称（默认函数名）
        log_input: 是否记录输入摘要
        log_output: 是否记录输出摘要
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 确定 agent 名称
            name = agent_name
            if not name and args and hasattr(args[0], "name"):
                name = args[0].name
            if not name:
                name = func.__qualname__.split(".")[0]

            act = action or func.__name__

            # 设置上下文
            agent_token = set_agent_name(name)

            # 输入摘要
            input_summary = None
            if log_input:
                parts = []
                for i, arg in enumerate(args[1:], 1):  # 跳过 self
                    s = str(arg)
                    parts.append(s[:100])
                for k, v in kwargs.items():
                    if k in ("callback", "on_progress"):
                        continue
                    s = str(v)
                    parts.append(f"{k}={s[:100]}")
                input_summary = ", ".join(parts)[:input_max_len]

            logger = get_logger(f"agent.{name.lower()}")
            start = time.time()

            # 指标
            registry.counter("agent_executions_total").inc(
                agent=name, action=act, status="started"
            )

            # 追踪
            with trace_agent(name, act) as span:
                span.set_attribute("action", act)
                if input_summary:
                    span.set_attribute("input_summary", input_summary[:200])

                try:
                    result = func(*args, **kwargs)
                    duration_ms = (time.time() - start) * 1000

                    # 输出摘要
                    output_summary = None
                    if log_output and result is not None:
                        output_summary = str(result)[:output_max_len]
                        span.set_attribute("output_summary", output_summary[:200])

                    # 日志
                    log_agent_execution(
                        agent_name=name,
                        action=act,
                        task_id=get_task_id(),
                        input_summary=input_summary,
                        output_summary=output_summary,
                        duration_ms=duration_ms,
                        status="success",
                    )

                    # 指标
                    registry.counter("agent_executions_total").inc(
                        agent=name, action=act, status="success"
                    )
                    registry.histogram("agent_duration_seconds").observe(
                        duration_ms / 1000, agent=name
                    )

                    # 写入数据库
                    _save_execution_log(
                        agent=name, action=act,
                        input_summary=input_summary,
                        output_summary=output_summary,
                        duration_ms=int(duration_ms),
                        status="success",
                    )

                    return result

                except Exception as e:
                    duration_ms = (time.time() - start) * 1000

                    log_agent_execution(
                        agent_name=name,
                        action=act,
                        task_id=get_task_id(),
                        input_summary=input_summary,
                        duration_ms=duration_ms,
                        status="error",
                        error=str(e),
                    )

                    registry.counter("agent_executions_total").inc(
                        agent=name, action=act, status="error"
                    )

                    _save_execution_log(
                        agent=name, action=act,
                        input_summary=input_summary,
                        duration_ms=int(duration_ms),
                        status="error",
                        error_message=str(e),
                    )

                    raise
                finally:
                    from .context import _agent_var
                    _agent_var.reset(agent_token)

        return wrapper
    return decorator


def log_model_call_decorator(provider: str = None):
    """
    模型调用日志装饰器

    用于包装模型客户端的 chat/completion 方法。
    自动记录 token、耗时、费用。
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            from .logger import log_model_call as _log_model

            model_name = kwargs.get("model", "unknown")
            prov = provider or "unknown"

            start = time.time()
            try:
                result = func(*args, **kwargs)
                latency = (time.time() - start) * 1000

                # 从 result 提取 token 信息
                input_tokens = 0
                output_tokens = 0
                if isinstance(result, dict):
                    usage = result.get("usage", {})
                    input_tokens = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)

                cost = _estimate_cost(model_name, input_tokens, output_tokens)

                _log_model(
                    model_name=model_name,
                    provider=prov,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency,
                    cost_estimate=cost,
                    status="success",
                )

                registry.counter("model_calls_total").inc(
                    model=model_name, provider=prov, status="success"
                )
                registry.histogram("model_call_duration_seconds").observe(
                    latency / 1000, model=model_name
                )
                registry.counter("model_tokens_total").inc(
                    model=model_name, type="input", amount=input_tokens
                )
                registry.counter("model_tokens_total").inc(
                    model=model_name, type="output", amount=output_tokens
                )
                registry.counter("model_cost_total").inc(
                    model=model_name, amount=cost
                )

                _save_model_log(
                    model_name=model_name, provider=prov,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    latency_ms=int(latency), cost_estimate=cost,
                    status="success",
                )

                return result

            except Exception as e:
                latency = (time.time() - start) * 1000
                _log_model(
                    model_name=model_name,
                    provider=prov,
                    latency_ms=latency,
                    status="error",
                    error_message=str(e),
                )
                registry.counter("model_calls_total").inc(
                    model=model_name, provider=prov, status="error"
                )
                _save_model_log(
                    model_name=model_name, provider=prov,
                    latency_ms=int(latency),
                    status="error", error_message=str(e),
                )
                raise

        return wrapper
    return decorator


def _estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """简单费用估算（每1K tokens价格，美元）"""
    # 简化的价格表
    prices = {
        "gpt-4o": {"input": 0.0025, "output": 0.01},
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
        "claude-3-opus": {"input": 0.015, "output": 0.075},
        "doubao-pro": {"input": 0.0008, "output": 0.002},
        "deepseek-chat": {"input": 0.00014, "output": 0.00028},
        "deepseek-reasoner": {"input": 0.00028, "output": 0.00084},
    }
    model_lower = model_name.lower()
    price = prices.get(model_lower, {"input": 0.001, "output": 0.002})
    # 模糊匹配
    for key, val in prices.items():
        if key in model_lower:
            price = val
            break
    return (input_tokens / 1000 * price["input"] +
            output_tokens / 1000 * price["output"])


def _save_execution_log(**kwargs):
    """异步保存执行日志到数据库"""
    try:
        import threading
        from ..database.session import SessionLocal
        from ..database.repository import ExecutionLogRepository
        from .context import get_request_id, get_task_id, get_trace_id

        def _save():
            try:
                session = SessionLocal()
                try:
                    repo = ExecutionLogRepository(session)
                    repo.log_execution(
                        request_id=get_request_id(),
                        trace_id=get_trace_id(),
                        **kwargs,
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                finally:
                    session.close()
            except Exception:
                pass

        t = threading.Thread(target=_save, daemon=True)
        t.start()
    except Exception:
        pass


def _save_model_log(**kwargs):
    """异步保存模型调用日志到数据库"""
    try:
        import threading
        from ..database.session import SessionLocal
        from ..database.repository import ModelCallLogRepository
        from .context import get_request_id, get_task_id, get_trace_id

        def _save():
            try:
                session = SessionLocal()
                try:
                    repo = ModelCallLogRepository(session)
                    repo.log_model_call(
                        request_id=get_request_id(),
                        trace_id=get_trace_id(),
                        task_id=get_task_id(),
                        **kwargs,
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                finally:
                    session.close()
            except Exception:
                pass

        t = threading.Thread(target=_save, daemon=True)
        t.start()
    except Exception:
        pass
