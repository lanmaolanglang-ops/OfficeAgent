"""P1-8 回归测试：模型能力必须与任务路由匹配。

历史缺陷：`ModelGateway.add_provider` 对每个 task_key 无差别
``model_ids.insert(0, config.id)``，纯文本模型（如 deepseek）会被塞进
VISION 路由。而 ``clients/base.py::analyze_image`` 对
``supports_vision=False`` 直接返回“不支持图片分析”——路由进去必然失败。

能力门槛口径（models/model_schemas.TASK_CAPABILITY_REQUIREMENTS）：
  * VISION -> supports_vision（硬门槛，绝不能退化到文本模型）
  * DOCUMENT_UNDERSTANDING -> supports_document（软门槛，仅排序优先）
    —— base.analyze_document 有通用文本回退，且 DEFAULT_ROUTING 本身
       就含不支持文档上传的模型，硬过滤会让既有配置失去全部候选。

全部使用 fake manager，不发起任何真实模型 API 调用。
"""
from dataclasses import replace

import pytest

from office_agent.model_gateway.gateway import ModelGateway
from office_agent.model_gateway.model_router import ModelRouter
from office_agent.models.model_schemas import (
    AITaskType,
    DEFAULT_MODEL_CONFIGS,
    DEFAULT_ROUTING,
    ModelProvider,
)


def _cfg(provider, **overrides):
    base = DEFAULT_MODEL_CONFIGS[provider]
    return replace(base, api_key="sk-test", **overrides)


class _FakeManager:
    """只实现 add_provider / select_model 真正用到的接口。"""

    def __init__(self, models=None, routing=None):
        self._models = {m.id: m for m in (models or [])}
        self._routing = routing if routing is not None else {
            task.value: list(ids) for task, ids in DEFAULT_ROUTING.items()
        }
        self.saved = 0

    def add_model(self, config):
        self._models[config.id] = config

    def get_model(self, model_id):
        return self._models.get(model_id)

    def get_routing(self, task_type):
        key = task_type.value if isinstance(task_type, AITaskType) else str(task_type)
        return list(self._routing.get(key, []))

    def list_available_models(self):
        return [m for m in self._models.values() if m.enabled and m.api_key]

    def get_default_model_id(self):
        return None

    def _save_config(self):
        self.saved += 1


def _gateway(manager):
    gw = ModelGateway.__new__(ModelGateway)
    gw.manager = manager
    gw.router = ModelRouter(manager)
    gw.failover = None
    gw.cancel_event = None
    gw.last_call = None
    return gw


# ------------------------------------------------------- add_provider


def test_text_only_provider_never_enters_vision_route():
    """核心用例：纯文本供应商不得进入视觉任务队列。"""
    mgr = _FakeManager([_cfg(ModelProvider.DEEPSEEK)])
    gw = _gateway(mgr)
    gw.add_provider("deepseek", "sk-test", model="deepseek-chat",
                    model_id="deepseek-default")

    assert "deepseek-default" not in mgr._routing[AITaskType.VISION.value]
    # 文本类任务不受影响
    assert "deepseek-default" in mgr._routing[AITaskType.SIMPLE_TEXT.value]
    assert "deepseek-default" in mgr._routing[AITaskType.FORMULA_GENERATION.value]
    assert "deepseek-default" in mgr._routing[AITaskType.CODE_GENERATION.value]
    assert "deepseek-default" in mgr._routing[AITaskType.PPT_CONTENT.value]
    assert "deepseek-default" in mgr._routing[AITaskType.CHINESE_WRITING.value]


def test_vision_and_document_provider_enters_every_route():
    mgr = _FakeManager([_cfg(ModelProvider.OPENAI)])
    gw = _gateway(mgr)
    gw.add_provider("openai", "sk-test", model="gpt-4o", model_id="openai-default")

    for task in AITaskType:
        assert "openai-default" in mgr._routing[task.value], task
    # 具备硬能力 -> 排在队首
    assert mgr._routing[AITaskType.VISION.value][0] == "openai-default"


def test_document_capable_but_text_only_stays_out_of_vision():
    """supports_document=True 但不支持视觉 -> 仍不得进视觉路由。"""
    mgr = _FakeManager([_cfg(ModelProvider.QWEN,
                             supports_document=True, supports_vision=False)])
    gw = _gateway(mgr)
    gw.add_provider("qwen", "sk-test", model_id="qwen-default")
    assert "qwen-default" not in mgr._routing[AITaskType.VISION.value]


def test_vision_only_provider_is_deferred_on_document_route():
    """软门槛：不具备文档能力的视觉模型仍可承接文档任务，但排在后面。"""
    mgr = _FakeManager([_cfg(ModelProvider.GEMINI, supports_document=False)])
    gw = _gateway(mgr)
    gw.add_provider("gemini", "sk-test", model_id="gemini-default")

    document_route = mgr._routing[AITaskType.DOCUMENT_UNDERSTANDING.value]
    vision_route = mgr._routing[AITaskType.VISION.value]
    assert document_route[-1] == "gemini-default"   # 软门槛：置底
    assert vision_route[0] == "gemini-default"      # 硬门槛：置顶


def test_explicit_capability_override_is_honoured():
    """自定义/兼容端点可显式声明能力。"""
    mgr = _FakeManager([_cfg(ModelProvider.DEEPSEEK)])
    gw = _gateway(mgr)
    gw.add_provider("deepseek", "sk-test", model_id="deepseek-vl",
                    supports_vision=True, supports_document=True)
    assert mgr._routing[AITaskType.VISION.value][0] == "deepseek-vl"
    assert mgr._routing[AITaskType.DOCUMENT_UNDERSTANDING.value][0] == "deepseek-vl"


def test_unknown_provider_without_capability_stays_out_of_vision():
    """没有默认模板且不显式声明 -> 能力未知，不得进入硬门槛任务。"""
    mgr = _FakeManager([])
    gw = _gateway(mgr)
    gw.add_provider("custom", "sk-test", model="my-model", model_id="custom-x")
    assert "custom-x" not in mgr._routing[AITaskType.VISION.value]
    assert "custom-x" in mgr._routing[AITaskType.SIMPLE_TEXT.value]


# ------------------------------------------------------- select_model


def _router(models, routing=None):
    mgr = _FakeManager(models, routing=routing)
    return ModelRouter(mgr), mgr


def test_router_filters_text_only_models_from_vision_task():
    """历史路由表里混进纯文本模型时，选型阶段也要纠正。"""
    models = [
        _cfg(ModelProvider.DEEPSEEK),
        _cfg(ModelProvider.OPENAI),
    ]
    routing = {task.value: list(ids) for task, ids in DEFAULT_ROUTING.items()}
    routing[AITaskType.VISION.value] = ["deepseek-default", "openai-default"]
    router, _ = _router(models, routing)

    selected = router.select_model(AITaskType.VISION)
    assert selected == ["openai-default"]


def test_router_vision_task_never_degrades_to_text_model():
    """没有任何视觉模型时宁可返回空，也不退化到纯文本模型。"""
    router, _ = _router([_cfg(ModelProvider.DEEPSEEK)])
    assert router.select_model(AITaskType.VISION) == []


def test_router_finds_vision_model_outside_routing_table():
    router, _ = _router([_cfg(ModelProvider.GEMINI)])
    assert router.select_model(AITaskType.VISION) == ["gemini-default"]


def test_router_text_tasks_are_unaffected():
    """文本/公式/代码类任务不得被能力过滤波及。"""
    models = [_cfg(ModelProvider.DEEPSEEK), _cfg(ModelProvider.OPENAI)]
    routing = {task.value: list(ids) for task, ids in DEFAULT_ROUTING.items()}
    routing[AITaskType.SIMPLE_TEXT.value] = ["deepseek-default", "openai-default"]
    router, _ = _router(models, routing)

    selected = router.select_model(AITaskType.SIMPLE_TEXT)
    assert selected == ["deepseek-default", "openai-default"]


def test_router_document_task_prefers_document_capable_model():
    models = [
        _cfg(ModelProvider.DEEPSEEK, supports_document=False),
        _cfg(ModelProvider.CLAUDE, supports_document=True),
    ]
    routing = {task.value: list(ids) for task, ids in DEFAULT_ROUTING.items()}
    routing[AITaskType.DOCUMENT_UNDERSTANDING.value] = [
        "deepseek-default", "claude-default",
    ]
    router, _ = _router(models, routing)

    selected = router.select_model(AITaskType.DOCUMENT_UNDERSTANDING)
    assert selected[0] == "claude-default"      # 软门槛：具备者优先
    assert set(selected) == {"claude-default", "deepseek-default"}


def test_router_prefer_model_without_vision_falls_through():
    """显式首选模型不具备视觉能力时，不得把它当视觉模型返回。"""
    models = [_cfg(ModelProvider.DEEPSEEK), _cfg(ModelProvider.GEMINI)]
    routing = {task.value: list(ids) for task, ids in DEFAULT_ROUTING.items()}
    router, _ = _router(models, routing)

    selected = router.select_model(
        AITaskType.VISION, prefer_model="deepseek-default"
    )
    assert "deepseek-default" not in selected
    assert selected == ["gemini-default"]


def test_router_require_vision_flag_still_works():
    router, _ = _router([_cfg(ModelProvider.DEEPSEEK), _cfg(ModelProvider.OPENAI)])
    selected = router.select_model(
        AITaskType.SIMPLE_TEXT, require_vision=True
    )
    assert selected == ["openai-default"]


@pytest.mark.parametrize("task", [
    AITaskType.SIMPLE_TEXT, AITaskType.FORMULA_GENERATION,
    AITaskType.CODE_GENERATION, AITaskType.PPT_CONTENT,
    AITaskType.CHINESE_WRITING,
])
def test_no_capability_gate_on_plain_text_tasks(task):
    """确认能力门槛只作用于声明过的任务，未声明任务保持原行为。"""
    models = [_cfg(ModelProvider.DEEPSEEK)]
    routing = {t.value: list(ids) for t, ids in DEFAULT_ROUTING.items()}
    routing[task.value] = ["deepseek-default"]
    router, _ = _router(models, routing)
    assert router.select_model(task) == ["deepseek-default"]
