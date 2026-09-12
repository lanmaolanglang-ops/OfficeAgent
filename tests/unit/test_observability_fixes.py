"""P3-4：observability 一组硬伤回归。

修复前：
- ``log_execution``/``log_model_call_decorator`` 无 iscoroutine 分支：
  装饰 async 函数时把"未 await 的 coroutine"当成功结果记录（耗时≈0、
  output 为 coroutine repr），协程体内的异常完全漏记；
- 非绑定方法的 ``args[0].name`` 劫持：普通函数的第一个参数恰好带
  ``.name`` 属性（如 ``Path``）时 agent 名被业务数据顶替；
- token usage 只认 prompt/completion_tokens（Claude 系与项目内客户端
  用 input/output_tokens）；
- ``model_tokens_total``/``model_cost_total`` 把 amount 当关键字传给
  ``inc(amount=1, **labels)`` → 运行时 TypeError，计量形同虚设；
- Prometheus exposition 的 label 值不转义（引号/反斜杠/换行）；
- tracer ``end_span`` 用 list.remove 而非栈 pop。
"""
import asyncio

import pytest

import office_agent.logging_system.decorators as decorators_module
from office_agent.logging_system.decorators import (
    log_execution,
    log_model_call_decorator,
)
from office_agent.logging_system.metrics import MetricsRegistry
from office_agent.logging_system.tracer import (
    get_trace_context,
    trace_span,
)


@pytest.fixture
def metrics_registry(monkeypatch):
    """装饰器默认用全局 registry，测试替换为隔离实例防串扰。"""
    fresh = MetricsRegistry()
    monkeypatch.setattr(decorators_module, "registry", fresh)
    return fresh


class TestLogExecution:
    def test_plain_function_name_not_hijacked(self):
        """普通函数的第一个参数带 .name（Path 等）不得顶替 agent 名。"""
        from pathlib import Path

        @log_execution()
        def process(document_path, extra):
            return "ok"

        with trace_span("test-root", "test"):
            assert process(Path("报告.docx"), "extra-value") == "ok"
            names = {s.name for s in get_trace_context().spans.values()}
        # agent 名只能来自 qualname 回退，不能被业务数据的 .name 劫持
        assert all("报告" not in name for name in names), names
        assert any(name.endswith("process") for name in names), names

    def test_bound_method_uses_self_name(self):
        @log_execution()
        def format_document(self, content):
            return content

        class Agent:
            name = "WordAgent"

        Agent.format_document = format_document
        with trace_span("test-root", "test"):
            assert Agent().format_document("正文") == "正文"
            names = {s.name for s in get_trace_context().spans.values()}
        assert "WordAgent.format_document" in names

    def test_plain_function_input_summary_includes_first_arg(self):
        @log_execution(action="demo")
        def process(first, second="s"):
            return "done"

        with trace_span("test-root", "test"):
            process("first-arg", second="kw-arg")
            attributes = [s.attributes for s in
                          get_trace_context().spans.values()
                          if s.attributes.get("action") == "demo"]
        assert attributes, "应产生执行 span"
        summary = attributes[0].get("input_summary", "")
        assert "first-arg" in summary, "普通函数的首个参数应计入输入摘要"
        assert "second=kw-arg" in summary

    def test_async_function_awaited_and_errors_captured(self):
        """async 函数：等待真实执行完毕，协程体内的异常必须被记录并抛出。"""
        calls = {"ok": 0, "error": 0}

        @log_execution(action="async_ok")
        async def async_ok():
            calls["ok"] += 1
            return "real-result"

        @log_execution(action="async_fail")
        async def async_fail():
            calls["error"] += 1
            raise ValueError("协程体内失败")

        async def scenario():
            with trace_span("test-root", "test"):
                assert await async_ok() == "real-result"
                with pytest.raises(ValueError):
                    await async_fail()
                spans = get_trace_context().spans.values()
                ok_span = next(s for s in spans
                               if s.attributes.get("action") == "async_ok")
                fail_span = next(s for s in spans
                                 if s.attributes.get("action") == "async_fail")
                return ok_span, fail_span

        ok_span, fail_span = asyncio.run(scenario())
        assert calls == {"ok": 1, "error": 1}
        # 修复前：output 是 coroutine repr、异常 span 根本不存在
        assert ok_span.attributes.get("output_summary") == "real-result"
        assert fail_span.status == "error"


class TestModelCallDecorator:
    def test_openai_style_usage_counted(self, metrics_registry):
        @log_model_call_decorator("openai")
        def chat(model="m1"):
            return {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}

        assert chat(model="m1") is not None
        counter = metrics_registry.counter("model_tokens_total")
        assert counter.get(model="m1", type="input") == 100
        assert counter.get(model="m1", type="output") == 50

    def test_claude_style_usage_counted(self, metrics_registry):
        @log_model_call_decorator("claude")
        def chat(model="m2"):
            return {"usage": {"input_tokens": 70, "output_tokens": 30}}

        chat(model="m2")
        counter = metrics_registry.counter("model_tokens_total")
        assert counter.get(model="m2", type="input") == 70
        assert counter.get(model="m2", type="output") == 30

    def test_token_amount_is_value_not_label(self, metrics_registry):
        """回归核心：amount 作为计量值而非标签（旧实现直接 TypeError）。"""
        @log_model_call_decorator("openai")
        def chat(model="m3"):
            return {"usage": {"prompt_tokens": 8, "completion_tokens": 2}}

        chat(model="m3")
        counter = metrics_registry.counter("model_tokens_total")
        assert counter.get(model="m3", type="input") == 8, \
            "token 计数必须按计量值累加（旧实现抛 TypeError）"
        assert metrics_registry.counter("model_calls_total").get(
            model="m3", provider="openai", status="success") == 1

    def test_missing_usage_defaults_to_zero(self, metrics_registry):
        @log_model_call_decorator("openai")
        def chat(model="m4"):
            return {"text": "no usage"}

        chat(model="m4")
        counter = metrics_registry.counter("model_tokens_total")
        assert counter.get(model="m4", type="input") == 0
        assert counter.get(model="m4", type="output") == 0

    def test_async_model_call(self, metrics_registry):
        @log_model_call_decorator("openai")
        async def achat(model="m5"):
            return {"usage": {"input_tokens": 6, "output_tokens": 4}}

        result = asyncio.run(achat(model="m5"))
        assert result["usage"]["input_tokens"] == 6
        counter = metrics_registry.counter("model_tokens_total")
        assert counter.get(model="m5", type="input") == 6


class TestPrometheusExposition:
    def test_label_values_escaped(self):
        registry = MetricsRegistry()
        counter = registry.counter("esc_test_total", labels=["agent"])
        counter.inc(agent='bad"quote\\slash\nnewline')
        text = registry.render_prometheus()
        line = next(line for line in text.splitlines()
                    if line.startswith("esc_test_total{"))
        assert line == 'esc_test_total{agent="bad\\"quote\\\\slash\\nnewline"} 1.0'
        body = line.split("{", 1)[1].split("}", 1)[0]
        assert "\n" not in body, "值内换行必须转义，exposition 不得断裂"

    def test_histogram_exposition_shape(self):
        registry = MetricsRegistry()
        hist = registry.histogram(
            "shape_test_seconds", buckets=[0.1, 1.0], labels=["model"])
        hist.observe(0.05, model="m")
        hist.observe(0.5, model="m")
        text = registry.render_prometheus()
        lines = [line for line in text.splitlines()
                 if line.startswith("shape_test_seconds")]
        assert 'shape_test_seconds_bucket{model="m",le="0.1"} 1' in lines
        assert 'shape_test_seconds_bucket{model="m",le="1.0"} 2' in lines
        assert 'shape_test_seconds_bucket{model="m",le="+Inf"} 2' in lines
        assert "shape_test_seconds_count 2" in lines


class TestTracerStack:
    def test_normal_lifo_nesting(self):
        import office_agent.logging_system.tracer as tracer_module

        ctx = tracer_module.TraceContext()
        outer = ctx.start_span("outer")
        inner = ctx.start_span("inner")
        assert ctx._current_stack == [outer.span_id, inner.span_id]
        ctx.end_span(inner)
        assert ctx._current_stack == [outer.span_id]
        ctx.end_span(outer)
        assert ctx._current_stack == []

    def test_out_of_order_end_keeps_tree_coherent(self):
        """乱序结束（外层先结束）：栈语义与树结构必须保持一致。"""
        import office_agent.logging_system.tracer as tracer_module

        ctx = tracer_module.TraceContext()
        outer = ctx.start_span("outer")
        inner = ctx.start_span("inner")
        ctx.end_span(outer)            # 外层先结束（异常路径可能出现）
        assert inner.span_id in ctx._current_stack, \
            "未结束的内层 span 仍应在栈上"
        late = ctx.start_span("late")
        assert late.parent_id == inner.span_id, \
            "新 span 的父级应是栈顶的未结束 span"
        ctx.end_span(late)
        ctx.end_span(inner)
        assert ctx._current_stack == []
        assert ctx.root is outer
        assert [child.name for child in outer.children] == ["inner"]
