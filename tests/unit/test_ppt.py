"""
PPT 生成任务单元测试

覆盖最近新增的两条路径：
- 页数解析 `_parse_slide_count`（从用户指令/简报提取期望页数）
- 按模板生成 `generate_with_template` 正确透传 slide_count
"""
import pytest
from pathlib import Path
from types import SimpleNamespace


@pytest.mark.unit
class TestParseSlideCount:
    """页数解析（纯函数，无 I/O）"""

    def _parse(self, instruction="", effective=""):
        from office_agent.task_queue.tasks.ppt_tasks import _parse_slide_count
        return _parse_slide_count(instruction, effective)

    def test_default_when_no_count(self):
        assert self._parse("帮我做一个产品介绍PPT") == 10

    def test_explicit_count_from_instruction(self):
        assert self._parse("做一份15页的汇报") == 15

    def test_count_from_effective_brief_when_instruction_empty(self):
        assert self._parse("", "生成8页的技术分享") == 8

    def test_instruction_takes_precedence_over_brief(self):
        # instruction 在前，先匹配到用户明确说的页数
        assert self._parse("20页", "10页") == 20

    def test_upper_bound_capped_at_50(self):
        assert self._parse("做100页") == 50
        assert self._parse("做51页") == 50

    def test_lower_bound_floor_at_1(self):
        assert self._parse("做0页") == 1

    def test_boundary_values(self):
        assert self._parse("做1页") == 1
        assert self._parse("做50页") == 50

    def test_whitespace_between_number_and_page(self):
        assert self._parse("15 页") == 15

    def test_no_false_positive_on_years(self):
        assert self._parse("2025年战略规划") == 10


@pytest.mark.unit
class TestGenerateWithTemplateSlideCount:
    """按模板生成时，页数应透传给内容规划器"""

    def test_slide_count_forwarded_to_planner(self, monkeypatch):
        from office_agent.ppt_agent.ppt_orchestrator import PPTOrchestrator
        from office_agent.ppt_agent.models import (
            PPTOutline, SlideContent, PPTGenerationResult,
        )

        orch = PPTOrchestrator()
        captured = {}

        def fake_plan_from_theme(theme, slide_count=10, style="professional",
                                 subtitle="", author=""):
            captured["slide_count"] = slide_count
            outline = PPTOutline(title=theme)
            outline.add_slide(SlideContent(layout="cover", title=theme))
            return outline

        def fake_generate(outline, output_path):
            return PPTGenerationResult(
                success=True, output_path=output_path, slide_count=len(outline.slides),
            )

        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(orch.planner, "plan_from_theme", fake_plan_from_theme)
        monkeypatch.setattr(orch.template_analyzer, "analyze", lambda path: None)
        monkeypatch.setattr(orch.template_analyzer, "apply_to_outline", lambda path, outline: outline)
        monkeypatch.setattr(orch.service, "generate", fake_generate)
        monkeypatch.setattr(orch.quality_checker, "check",
                            lambda output_path, expected_slides=None: SimpleNamespace(score=90.0))

        result = orch.generate_with_template(
            template_path="fake.pptx", theme="产品介绍", slide_count=18,
        )

        assert captured["slide_count"] == 18
        assert result.success is True

    def test_default_slide_count_is_10(self, monkeypatch):
        from office_agent.ppt_agent.ppt_orchestrator import PPTOrchestrator
        from office_agent.ppt_agent.models import (
            PPTOutline, SlideContent, PPTGenerationResult,
        )

        orch = PPTOrchestrator()
        captured = {}

        def fake_plan_from_theme(theme, slide_count=10, style="professional",
                                 subtitle="", author=""):
            captured["slide_count"] = slide_count
            outline = PPTOutline(title=theme)
            outline.add_slide(SlideContent(layout="cover", title=theme))
            return outline

        monkeypatch.setattr(Path, "exists", lambda self: True)
        monkeypatch.setattr(orch.planner, "plan_from_theme", fake_plan_from_theme)
        monkeypatch.setattr(orch.template_analyzer, "analyze", lambda path: None)
        monkeypatch.setattr(orch.template_analyzer, "apply_to_outline", lambda path, outline: outline)
        monkeypatch.setattr(orch.service, "generate",
                            lambda outline, output_path: PPTGenerationResult(
                                success=True, output_path=output_path, slide_count=len(outline.slides)))
        monkeypatch.setattr(orch.quality_checker, "check",
                            lambda output_path, expected_slides=None: SimpleNamespace(score=90.0))

        orch.generate_with_template(template_path="fake.pptx", theme="产品介绍")

        assert captured["slide_count"] == 10


class TestPPTImageGeneration:
    def test_content_list_is_promoted_to_image_layout(self, tmp_path):
        from office_agent.ppt_agent.models import PPTOutline, SlideContent
        from office_agent.ppt_agent.ppt_orchestrator import PPTOrchestrator

        image = tmp_path / "image.png"
        image.write_bytes(b"image")

        class FakeGateway:
            def available(self):
                return True

            def generate(self, *_args, **_kwargs):
                return str(image)

        outline = PPTOutline(title="测试")
        outline.add_slide(SlideContent(
            layout="content_list", title="内容页", bullets=["要点一", "要点二"]
        ))
        orchestrator = PPTOrchestrator(image_gateway=FakeGateway(), max_generated_images=1)
        result = orchestrator._generate_marked_images(outline)

        assert result.slides[0].layout == "content_image"
        assert result.slides[0].image_path == str(image)
        assert orchestrator.image_generation["generated"] == 1

    def test_fatal_image_error_stops_repeated_calls(self):
        from office_agent.ppt_agent.models import PPTOutline, SlideContent
        from office_agent.ppt_agent.ppt_orchestrator import PPTOrchestrator

        class FailingGateway:
            calls = 0

            def available(self):
                return True

            def generate(self, *_args, **_kwargs):
                self.calls += 1
                raise RuntimeError("图像服务连接失败: getaddrinfo failed")

        gateway = FailingGateway()
        outline = PPTOutline(title="测试")
        for index in range(3):
            outline.add_slide(SlideContent(
                layout="content", title=f"内容页{index}", bullets=["要点"]
            ))
        orchestrator = PPTOrchestrator(image_gateway=gateway, max_generated_images=3)
        orchestrator._generate_marked_images(outline)

        assert gateway.calls == 1
        assert orchestrator.image_generation["attempted"] == 1
