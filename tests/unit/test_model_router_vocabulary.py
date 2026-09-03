"""model_router 词表向 api.routing 单一真相源收敛的专项测试（清单 2.1）。

钉住两件事：
1. 词表血缘——模型选型层的产品词必须包含 API 路由层的强关键词，
   不允许两层再各自漂移；
2. 跨层一致——同一输入在 API 路由（选 Agent）与模型路由（选模型）
   对产品归属的判定不再互相矛盾。
"""
from office_agent.api.routing import PPT_STRONG, WORD_STRONG, route_intent
from office_agent.model_gateway.model_router import (
    DOCUMENT_KEYWORDS, PPT_KEYWORDS, ModelRouter,
)
from office_agent.models.model_schemas import AITaskType


def _router() -> ModelRouter:
    # infer_task_type 不访问 model_manager，传 None 即可
    return ModelRouter(None)


class TestVocabularyProvenance:
    def test_document_keywords_include_api_word_strong(self):
        assert set(WORD_STRONG) <= set(DOCUMENT_KEYWORDS)

    def test_ppt_keywords_include_api_ppt_strong(self):
        assert set(PPT_STRONG) <= set(PPT_KEYWORDS)

    def test_keywords_are_module_level_constants(self):
        # 词表必须在模块级唯一处定义（函数内不再藏私有副本）
        import inspect

        from office_agent.model_gateway import model_router
        src = inspect.getsource(model_router.ModelRouter.infer_task_type)
        assert "vision_keywords =" not in src
        assert "doc_keywords =" not in src
        assert "ppt_keywords =" not in src


class TestCrossLayerConsistency:
    def test_ppt_input_consistent(self):
        text = "帮我做一份季度汇报PPT"
        agent, _, _ = route_intent(text)
        assert agent == "ppt_agent"
        assert _router().infer_task_type(text) == AITaskType.PPT_CONTENT

    def test_word_input_consistent(self):
        text = "把这份word文档重新排版"
        agent, _, _ = route_intent(text)
        assert agent == "word_agent"
        assert _router().infer_task_type(text) == AITaskType.DOCUMENT_UNDERSTANDING

    def test_pptx_synonym_now_recognized(self):
        # 收敛前 "演示文稿"/"pptx" 只在 API 层识别，模型层漏判
        assert _router().infer_task_type("写一份演示文稿") == AITaskType.PPT_CONTENT

    def test_gang_outline_conflict_single_decision(self):
        # "大纲"同属文档/PPT 词表：裁决顺序唯一（文档先于 PPT），钉住不再漂移
        assert _router().infer_task_type("帮我列一个大纲") == AITaskType.DOCUMENT_UNDERSTANDING

    def test_neutral_text_falls_through_both_layers(self):
        text = "今天天气怎么样"
        agent, _, _ = route_intent(text)
        assert agent == "orchestrator"
        assert _router().infer_task_type(text) == AITaskType.SIMPLE_TEXT
