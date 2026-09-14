"""
日志装饰器

提供 Agent 执行日志、模型调用日志的装饰器，不侵入业务代码。

用法：
    from office_agent.logging_system.decorators import log_execution

    class WordAgent:
        @log_execution(action="format_document")
        def format_document(self, content, **kwargs):
            ...

同步与 async 函数均受支持：async 函数会在真正执行完毕后记录结果与
异常，而不是把未 await 的 coroutine 当成"成功结果"。
"""
import time
import functools
import inspect
from typing import Callable

from .logger import log_agent_execution
from .context import (
    set_agent_name, get_task_id,
)
from .metrics import registry
from .tracer import trace_agent


def _resolve_agent_name(func: Callable, args: tuple,
                        agent_name: str | None) -> str:
    """确定 agent 名称。

    只有当 ``args[0]`` 是"其类型以同名方法提供被装饰函数"的实例（即它
    真的是 self）时才读取其 ``name`` 属性；普通函数的 ``args[0]`` 是
    业务数据（任何带 ``.name`` 属性的对象——例如 ``Path``——都会顶替
    agent 名称），一律走 qualname 回退。
    """
    if agent_name:
        return agent_name
    if args and hasattr(args[0], "name"):
        bound = getattr(type(args[0]), func.__name__, None)
        if bound is not None and getattr(bound, "__wrapped__", None) is func:
            return getattr(args[0], "name")
    return func.__qualname__.split(".")[0]


# 输入摘要中不得原样落入日志/DB 的敏感键
_SENSITIVE_INPUT_KEYS = frozenset({
    "api_key", "apikey", "token", "password", "secret",
    "authorization", "jwt", "access_token", "refresh_token",
})


def _summarize_input(func: Callable, args: tuple, kwargs: dict,
                     log_input: bool, input_max_len: int) -> str | None:
    if not log_input:
        return None
    # 方法跳过 self；普通函数从第一个参数开始记录
    start = 1 if inspect.ismethod(func) else 0
    parts = [str(arg)[:100] for arg in args[start:]]
    for key, value in kwargs.items():
        if key in ("callback", "on_progress"):
            continue
        if key.lower() in _SENSITIVE_INPUT_KEYS:
            parts.append(f"{key}=[已隐藏]")
            continue
        parts.append(f"{key}={str(value)[:100]}")
    return ", ".join(parts)[:input_max_len]


def _extract_token_usage(result) -> tuple[int, int]:
    """从模型返回值提取 (input_tokens, output_tokens)。

    provider 命名不一：OpenAI 系用 prompt_tokens/completion_tokens，
    Claude 系与项目内模型客户端用 input_tokens/output_tokens，两者都认。
    """
    if not isinstance(result, dict):
        return 0, 0
    usage = result.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    try:
        return int(input_tokens or 0), int(output_tokens or 0)
    except (TypeError, ValueError):
        return 0, 0


def log_execution(agent_name: str | None = None, action: str | None = None,
                  log_input: bool = False, log_output: bool = True,
                  input_max_len: int = 500, output_max_len: int = 500):
    """
    Agent 执行日志装饰器

    ``log_input`` 默认 False（P2-64）：输入可能含用户数据/密钥/prompt，
    需要完整摘要的调用方必须显式 opt-in，且仍经敏感键脱敏。
    """
    def decorator(func: Callable) -> Callable:
        is_coroutine = inspect.iscoroutinefunction(func)

        def _emit_success(name, act, input_summary, output_summary,
                          duration_ms, span):
            log_agent_execution(
                agent_name=name,
                action=act,
                task_id=get_task_id(),
                input_summary=input_summary,
                output_summary=output_summary,
                duration_ms=duration_ms,
                status="success",
            )
            registry.counter("agent_executions_total").inc(
                agent=name, action=act, status="success"
            )
            registry.histogram("agent_duration_seconds").observe(
                duration_ms / 1000, agent=name
            )
            _save_execution_log(
                agent=name, action=act,
                input_summary=input_summary,
                output_summary=output_summary,
                duration_ms=int(duration_ms),
                status="success",
            )

        def _emit_error(name, act, input_summary, duration_ms, error):
            log_agent_execution(
                agent_name=name,
                action=act,
                task_id=get_task_id(),
                input_summary=input_summary,
                duration_ms=duration_ms,
                status="error",
                error=str(error),
            )
            registry.counter("agent_executions_total").inc(
                agent=name, action=act, status="error"
            )
            _save_execution_log(
                agent=name, action=act,
                input_summary=input_summary,
                duration_ms=int(duration_ms),
                status="error",
                error_message=str(error),
            )

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            name = _resolve_agent_name(func, args, agent_name)
            act = action or func.__name__
            agent_token = set_agent_name(name)
            input_summary = _summarize_input(func, args, kwargs,
                                             log_input, input_max_len)

            start = time.time()
            registry.counter("agent_executions_total").inc(
                agent=name, action=act, status="started"
            )

            try:
                with trace_agent(name, act) as span:
                    span.set_attribute("action", act)
                    if input_summary:
                        span.set_attribute("input_summary",
                                           input_summary[:200])
                    try:
                        result = await func(*args, **kwargs)
                        duration_ms = (time.time() - start) * 1000
                        output_summary = None
                        if log_output and result is not None:
                            output_summary = str(result)[:output_max_len]
                            span.set_attribute("output_summary",
                                               output_summary[:200])
                        _emit_success(name, act, input_summary,
                                      output_summary, duration_ms, span)
                        return result
                    except Exception as e:
                        duration_ms = (time.time() - start) * 1000
                        _emit_error(name, act, input_summary,
                                    duration_ms, e)
                        raise
            finally:
                from .context import _agent_var
                _agent_var.reset(agent_token)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            name = _resolve_agent_name(func, args, agent_name)
            act = action or func.__name__
            agent_token = set_agent_name(name)
            input_summary = _summarize_input(func, args, kwargs,
                                             log_input, input_max_len)

            start = time.time()
            registry.counter("agent_executions_total").inc(
                agent=name, action=act, status="started"
            )

            try:
                with trace_agent(name, act) as span:
                    span.set_attribute("action", act)
                    if input_summary:
                        span.set_attribute("input_summary",
                                           input_summary[:200])
                    try:
                        result = func(*args, **kwargs)
                        duration_ms = (time.time() - start) * 1000
                        output_summary = None
                        if log_output and result is not None:
                            output_summary = str(result)[:output_max_len]
                            span.set_attribute("output_summary",
                                               output_summary[:200])
                        _emit_success(name, act, input_summary,
                                      output_summary, duration_ms, span)
                        return result
                    except Exception as e:
                        duration_ms = (time.time() - start) * 1000
                        _emit_error(name, act, input_summary,
                                    duration_ms, e)
                        raise
            finally:
                from .context import _agent_var
                _agent_var.reset(agent_token)

        return async_wrapper if is_coroutine else wrapper
    return decorator


def log_model_call_decorator(provider: str | None = None):
    """
    模型调用日志装饰器

    用于包装模型客户端的 chat/completion 方法（同步与 async 均可）。
    自动记录 token、耗时、费用。
    """
    def decorator(func: Callable) -> Callable:
        is_coroutine = inspect.iscoroutinefunction(func)

        def _emit_success(model_name, prov, result, latency):
            from .logger import log_model_call as _log_model

            input_tokens, output_tokens = _extract_token_usage(result)
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
            # amount 必须作为 inc 的第一个位置参数；作为关键字传入会与
            # 形参 amount 冲突（TypeError），token/cost 从此失去计量。
            registry.counter("model_tokens_total").inc(
                input_tokens, model=model_name, type="input"
            )
            registry.counter("model_tokens_total").inc(
                output_tokens, model=model_name, type="output"
            )
            registry.counter("model_cost_total").inc(
                cost, model=model_name
            )

        def _emit_error(model_name, prov, latency, error):
            from .logger import log_model_call as _log_model

            _log_model(
                model_name=model_name,
                provider=prov,
                latency_ms=latency,
                status="error",
                error_message=str(error),
            )
            registry.counter("model_calls_total").inc(
                model=model_name, provider=prov, status="error"
            )

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            model_name = kwargs.get("model", "unknown")
            prov = provider or "unknown"
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                _emit_success(model_name, prov, result,
                              (time.time() - start) * 1000)
                return result
            except Exception as e:
                _emit_error(model_name, prov, (time.time() - start) * 1000, e)
                raise

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            model_name = kwargs.get("model", "unknown")
            prov = provider or "unknown"
            start = time.time()
            try:
                result = func(*args, **kwargs)
                _emit_success(model_name, prov, result,
                              (time.time() - start) * 1000)
                return result
            except Exception as e:
                _emit_error(model_name, prov, (time.time() - start) * 1000, e)
                raise

        return async_wrapper if is_coroutine else wrapper
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
        from .background import submit as submit_background
        from ..database.session import SessionLocal
        from ..database.repository import ExecutionLogRepository
        from .context import get_request_id, get_trace_id

        request_id = get_request_id()
        trace_id = get_trace_id()

        def _save():
            try:
                session = SessionLocal()
                try:
                    repo = ExecutionLogRepository(session)
                    repo.log_execution(
                        request_id=request_id,
                        trace_id=trace_id,
                        **kwargs,
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                finally:
                    session.close()
            except Exception:
                pass

        submit_background(_save)
    except Exception:
        pass
