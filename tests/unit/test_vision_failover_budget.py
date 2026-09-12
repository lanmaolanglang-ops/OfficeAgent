"""P3-8：VisionGateway 故障转移预算回归。

修复前：``analyze`` 对 ``client_order`` 里的全部候选逐个尝试且无任何
上限——N 个 provider、每页全量转移，多页并发时总真实请求次数无界；
最终错误也只携带最后一个错误，丢掉 attempt 历史。

预算语义：候选按 client_order 顺序消耗预算（默认 failover_budget=3，
可用构造参数或请求级 max_attempts 覆盖，请求级不写回全局）；预算
耗尽后剩余候选不再尝试，最终错误带 (尝试 x/y) 与逐 provider 错误史。
"""

from office_agent.vision_gateway.gateway import VisionGateway
from office_agent.vision_gateway.vision_models import VisionRequest


class _StubClient:
    def __init__(self, name, fail_times=0, error="boom"):
        self.name = name
        self.fail_times = fail_times
        self.error = error
        self.calls = 0
        self.model = name
        self.display_name = name
        self.provider_name = name.split(":")[0]

    def analyze(self, request):
        self.calls += 1
        from office_agent.vision_gateway.vision_models import VisionResponse
        if self.calls <= self.fail_times:
            return VisionResponse(success=False, error=self.error)
        return VisionResponse(success=True, content=f"ok-from-{self.name}")


def _request():
    return VisionRequest(images=[], prompt="p")


def _gateway_with(names, fail_map=None, budget=None):
    gateway = VisionGateway(failover_budget=budget) if budget else VisionGateway()
    fail_map = fail_map or {}
    for name in names:
        client = _StubClient(name, fail_times=fail_map.get(name, 0),
                             error=f"{name} 挂了")
        gateway.add_custom(client, name=name)
    return gateway


class TestFailoverBudget:
    def test_primary_success_costs_one_attempt(self):
        gateway = _gateway_with(["a", "b", "c"], fail_map={"a": 0})
        result = gateway.analyze(_request())
        assert result.success is True
        assert result.text == "ok-from-a"
        assert gateway.clients["b"].calls == 0
        assert gateway.clients["c"].calls == 0

    def test_transient_failover_to_second_provider(self):
        gateway = _gateway_with(["a", "b"], fail_map={"a": 1})
        result = gateway.analyze(_request())
        assert result.success is True
        assert result.text == "ok-from-b"
        assert gateway.clients["a"].calls == 1

    def test_budget_bounds_total_attempts(self):
        """3 个候选、预算 2：最多消耗 2 次尝试，第 3 个不再调用。"""
        gateway = _gateway_with(["a", "b", "c"],
                                fail_map={"a": 1, "b": 1, "c": 1},
                                budget=2)
        result = gateway.analyze(_request())
        assert result.success is False
        assert gateway.clients["a"].calls == 1
        assert gateway.clients["b"].calls == 1
        assert gateway.clients["c"].calls == 0, "预算耗尽后不得继续尝试"

    def test_exhausted_error_carries_attempt_history(self):
        gateway = _gateway_with(["a", "b", "c"], fail_map={n: 1 for n in "abc"})
        result = gateway.analyze(_request())
        assert result.success is False
        for name in ("a", "b"):
            assert f"{name}: {name} 挂了" in result.error
        assert "尝试" in result.error and "预算" in result.error

    def test_request_level_budget_does_not_touch_global(self):
        """请求级预算=1 只试首选；全局预算请求照常按 3 个候选转移。"""
        gateway = _gateway_with(["a", "b", "c"],
                                fail_map={n: 99 for n in "abc"})
        result = gateway.analyze(_request(), max_attempts=1)
        assert result.success is False
        assert gateway.clients["a"].calls == 1
        assert gateway.clients["b"].calls == 0, "请求级预算=1 不得尝试第二候选"
        assert gateway.failover_budget == 3, "请求级预算不写回全局配置"

        # 全局预算仍然生效（恒败桩：3 个候选各试 1 次）
        result = gateway.analyze(_request())
        assert result.success is False
        assert gateway.clients["a"].calls == 2
        assert gateway.clients["b"].calls == 1
        assert gateway.clients["c"].calls == 1

    def test_budget_one_fails_fast(self):
        gateway = _gateway_with(["a", "b", "c"],
                                fail_map={n: 1 for n in "abc"}, budget=1)
        result = gateway.analyze(_request(), max_attempts=1)
        assert gateway.clients["b"].calls == 0

    def test_concurrent_requests_have_independent_attempts(self):
        """并发请求各自消耗自己的预算（计数在调用栈内，不共享）。"""
        import threading
        gateway = _gateway_with(["a", "b", "c"],
                                fail_map={n: 1 for n in "abc"}, budget=1)
        threads = [threading.Thread(
            target=lambda: gateway.analyze(_request(), max_attempts=1))
            for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # 每个请求独立尝试 a 一次 → a 共 4 次；b/c 始终不参与
        assert gateway.clients["a"].calls == 4
        assert gateway.clients["b"].calls == 0

    def test_single_model_key_ignores_budget_semantics(self):
        """显式指定模型：只尝试该模型（预算不影响单候选行为）。"""
        gateway = _gateway_with(["a", "b"], fail_map={"a": 1})
        result = gateway.analyze(_request(), model_key="a", max_attempts=1)
        assert result.success is False
        assert gateway.clients["b"].calls == 0
