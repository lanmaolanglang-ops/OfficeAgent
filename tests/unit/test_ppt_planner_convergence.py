"""PPT Planner Convergence regression tests."""

from __future__ import annotations

from types import SimpleNamespace

from office_agent.ppt_agent.content_planner import ContentPlanner
from office_agent.ppt_agent.models import PPTOutline, SlideContent
from office_agent.ppt_agent.ppt_orchestrator import PPTOrchestrator
from office_agent.ppt_agent.slide_planner import SlidePlanner, SlidePlan


def _gateway(content="", success=True):
    return SimpleNamespace(
        chat=lambda **kwargs: SimpleNamespace(
            success=success,
            content=content,
            error="" if success else "simulated failure",
            raw_response={},
        ),
    )


def _assert_plan_shape(outline: PPTOutline):
    assert outline.title
    assert outline.slides
    assert outline.slides[0].layout == "cover"
    assert outline.slides[-1].layout == "summary"
    for index, slide in enumerate(outline.slides, 1):
        assert slide.page_number == index


class TestAuthoritativeContentPlanner:
    def test_simple_chinese_theme(self):
        outline = ContentPlanner().plan_from_theme(
            "人工智能发展趋势", slide_count=8, style="professional",
        )
        _assert_plan_shape(outline)

    def test_english_theme(self):
        outline = ContentPlanner().plan_from_theme(
            "AI Product Launch", slide_count=6, style="professional",
        )
        _assert_plan_shape(outline)

    def test_mixed_language_theme(self):
        outline = ContentPlanner().plan_from_theme(
            "AI 项目 Roadmap", slide_count=7, style="professional",
        )
        _assert_plan_shape(outline)

    def test_multipage_outline_budget(self):
        outline = ContentPlanner().plan_from_theme(
            "年度战略规划", slide_count=12, style="professional",
        )
        assert len(outline.slides) <= 12

    def test_chart_and_table_intent_are_preserved(self):
        outline = ContentPlanner().plan_from_outline_data("数据", [{
            "layout": "chart",
            "title": "销售趋势",
            "chart_type": "line",
            "chart_categories": ["Q1", "Q2"],
            "chart_series": [("收入", [1, 2])],
            "table_data": [["指标", "值"], ["收入", "100"]],
        }])
        slide = outline.slides[0]
        assert slide.chart_type == "line"
        assert slide.chart_categories == ["Q1", "Q2"]
        assert slide.table_data == [["指标", "值"], ["收入", "100"]]

    def test_image_intent_is_preserved(self):
        outline = ContentPlanner().plan_from_outline_data("图文", [{
            "layout": "content_image",
            "title": "产品示意",
            "image_prompt": "横向构图，商务风格，无文字",
        }])
        assert outline.slides[0].image_prompt == "横向构图，商务风格，无文字"

    def test_failure_falls_back_to_template_and_is_marked(self):
        planner = ContentPlanner(model_gateway=_gateway(success=False))
        outline = planner.plan_from_theme("项目汇报", slide_count=8)
        _assert_plan_shape(outline)
        assert outline.used_template is True

    def test_malformed_llm_response_falls_back_without_crash(self):
        planner = ContentPlanner(model_gateway=_gateway(content="not json"))
        outline = planner.plan_from_theme("项目汇报", slide_count=8)
        _assert_plan_shape(outline)
        assert outline.used_template is True


class TestConvergedProductionPath:
    def test_initial_and_revision_paths_share_content_planner(self):
        orchestrator = PPTOrchestrator()
        assert isinstance(orchestrator.planner, ContentPlanner)

    def test_legacy_slide_planner_delegates_to_content_planner(self, monkeypatch):
        captured = {}

        def fake_plan_from_theme(self, theme, slide_count=10,
                                 style="professional", subtitle="", author=""):
            captured.update(theme=theme, slide_count=slide_count, style=style)
            outline = PPTOutline(title=theme, theme=style)
            outline.add_slide(SlideContent(layout="cover", title=theme))
            outline.add_slide(SlideContent(layout="summary", title="感谢聆听"))
            return outline

        monkeypatch.setattr(
            ContentPlanner, "plan_from_theme", fake_plan_from_theme,
        )
        plan = SlidePlanner().plan("演示", slide_count=5, style="academic")
        assert isinstance(plan, SlidePlan)
        assert captured == {
            "theme": "演示", "slide_count": 5, "style": "academic",
        }
        assert [slide.layout for slide in plan.slides] == ["cover", "summary"]
