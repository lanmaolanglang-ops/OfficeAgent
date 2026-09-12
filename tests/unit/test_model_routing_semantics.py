"""P3-5/P3-6：路由防副本与 failover 语义回归。

P3-5 修复前：``get_routing`` 直接返回 ``self._routing``/``DEFAULT_ROUTING``
的活引用——调用方为单次请求重排/剔除候选即永久改写全局默认路由；
``set_routing`` 按引用存储调用方列表且无任何校验。

P3-6 修复前：重试判定用 ``"http 5"`` 裸子串匹配 HTTP 状态码；
``infer_task_type`` 的公式词表以裸子串匹配（"sum" ⊂ "summary"，
"计算/平均/函数" 过泛）造成误路由。
"""
import pytest

from office_agent.models.model_schemas import AITaskType, DEFAULT_ROUTING
from office_agent.model_gateway.failover import FailoverManager
from office_agent.model_gateway.model_manager import ModelManager
from office_agent.model_gateway.model_router import ModelRouter


@pytest.fixture
def manager(tmp_path):
    """隔离的 ModelManager：models.json/密钥全部落在 tmp_path。"""
    return ModelManager(config_dir=str(tmp_path / "models"))


@pytest.fixture
def router(manager):
    return ModelRouter(manager)


class TestRoutingDefensiveCopies:
    def test_get_routing_returns_copy_of_custom_routing(self, manager):
        manager.set_routing(AITaskType.SIMPLE_TEXT, ["a", "b"])
        first = manager.get_routing(AITaskType.SIMPLE_TEXT)
        first.append("污染")
        first.reverse()
        assert manager.get_routing(AITaskType.SIMPLE_TEXT) == ["a", "b"], \
            "调用方修改返回列表不得污染 canonical 路由"

    def test_get_routing_returns_copy_of_default_routing(self, manager):
        task_type = AITaskType.DOCUMENT_UNDERSTANDING
        snapshot = list(DEFAULT_ROUTING[task_type])
        returned = manager.get_routing(task_type)
        returned.append("污染条目")
        returned.clear()
        assert DEFAULT_ROUTING[task_type] == snapshot, \
            "默认路由的全局定义不得被请求侧修改"
        assert manager.get_routing(task_type) == snapshot

    def test_set_routing_rejects_invalid_payload(self, manager):
        with pytest.raises(ValueError):
            manager.set_routing(AITaskType.SIMPLE_TEXT, [])
        with pytest.raises(ValueError):
            manager.set_routing(AITaskType.SIMPLE_TEXT, "openai-default")
        with pytest.raises(ValueError):
            manager.set_routing(AITaskType.SIMPLE_TEXT, ["ok", 3, None])
        assert manager.get_routing(AITaskType.SIMPLE_TEXT) == \
            list(DEFAULT_ROUTING[AITaskType.SIMPLE_TEXT])

    def test_set_routing_stores_defensive_copy(self, manager):
        caller_list = ["x-model", "y-model"]
        manager.set_routing(AITaskType.SIMPLE_TEXT, caller_list)
        caller_list.append("后加的")
        caller_list.clear()
        assert manager.get_routing(AITaskType.SIMPLE_TEXT) == \
            ["x-model", "y-model"], \
            "set 之后调用方修改自己的列表不得影响已保存路由"

    def test_explicit_update_still_persists(self, manager):
        manager.set_routing(AITaskType.SIMPLE_TEXT, ["only-model"])
        assert manager.get_routing(AITaskType.SIMPLE_TEXT) == ["only-model"]


class TestRetryableClassification:
    @pytest.mark.parametrize("error,retryable", [
        ("HTTP 500: internal", True),
        ("HTTP 502: bad gateway", True),
        ("HTTP 429: rate limited", True),
        ("HTTP 408: timeout", True),
        ("HTTP 409: conflict", True),
        ("HTTP 425: too early", True),
        ("HTTP 400: bad request", False),
        ("HTTP 401: unauthorized", False),
        ("HTTP 403: forbidden", False),
        ("HTTP 404: not found", False),
        ("HTTP 451: unavailable for legal reasons", False),
        ("timeout", True),
        ("rate limit exceeded", True),
        ("连接错误", True),
        ("鉴权失败", False),
        ("api key invalid", False),
        ("", True),
    ])
    def test_classification(self, error, retryable):
        assert FailoverManager._is_retryable_error(error) is retryable

    def test_http_code_beats_substring_ambiguity(self):
        """结构化状态码优先于文本子串：HTTP 4xx 永远不因裸子串被误判重试。"""
        assert FailoverManager._is_retryable_error(
            "HTTP 404: not found (attempt 5)") is False
        # "http 5" 裸子串连 "HTTP 5xx" 以外的语义也命中不了 4xx 段
        assert FailoverManager._is_retryable_error("HTTP 503") is True


class TestFormulaRoutingPrecision:
    def test_summary_is_not_formula(self, router):
        """回归核心：'sum' ⊂ 'summary' 不得再把总结类文本路由到公式。"""
        assert router.infer_task_type("帮我总结 summary 报告") \
            is not AITaskType.FORMULA_GENERATION

    def test_generic_words_are_not_formula(self, router):
        assert router.infer_task_type("写一份关于大数据计算的汇报") \
            is not AITaskType.FORMULA_GENERATION
        assert router.infer_task_type("两个方案的平均对比") \
            is not AITaskType.FORMULA_GENERATION

    def test_real_formula_still_routes(self, router):
        assert router.infer_task_type("用 vlookup 匹配两列数据") \
            == AITaskType.FORMULA_GENERATION
        assert router.infer_task_type("写个公式求和") \
            == AITaskType.FORMULA_GENERATION
        assert router.infer_task_type("用 SUM(A1:A9) 统计销售额") \
            == AITaskType.FORMULA_GENERATION
        assert router.infer_task_type("帮我写 excel 函数") \
            == AITaskType.FORMULA_GENERATION

    def test_locked_behaviors_unchanged(self, router):
        """既有裁决顺序与词表行为不回归（test_model_router_vocabulary）。"""
        assert router.infer_task_type("请分析截图") == AITaskType.VISION
        assert router.infer_task_type("生成 Python 代码") \
            == AITaskType.CODE_GENERATION
        assert router.infer_task_type("写一份演示文稿") == AITaskType.PPT_CONTENT
        assert router.infer_task_type("帮我列一个大纲") \
            == AITaskType.DOCUMENT_UNDERSTANDING
        assert router.infer_task_type("今天天气怎么样") \
            == AITaskType.SIMPLE_TEXT


class TestFailoverSemantics:
    def test_retry_is_same_target_failover_is_next_target(self):
        """钉住 canonical 语义：retry 同目标、failover 切下一目标。"""

        class _Manager:
            def __init__(self):
                self.calls = []

            def get_routing(self, _task_type):
                return ["primary", "backup"]

            def get_model(self, model_id):
                from types import SimpleNamespace
                return SimpleNamespace(enabled=True, api_key="key")

            def get_client(self, model_id):
                return object()

        manager = _Manager()

        def action(client, **kwargs):
            manager.calls.append("attempt")
            from office_agent.models.model_schemas import ModelResponse
            # 首选模型首次即确定性失败（不重试同目标），随后备用成功
            if len(manager.calls) == 1:
                return ModelResponse(success=False, error="HTTP 400: bad")
            return ModelResponse(success=True, content="ok",
                                 model_used="backup")

        failover = FailoverManager(manager, max_retries=3)
        response = failover.execute_with_failover(AITaskType.SIMPLE_TEXT, action)
        assert response.success is True
        meta = (response.raw_response or {}).get("_office_agent", {})
        assert meta["attempted_models"] == ["primary", "backup"], \
            "确定性失败必须 failover 到下一模型而不是原地重试"
        assert meta["fallback_used"] is True
