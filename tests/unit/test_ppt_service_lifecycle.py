"""
PPT 生命周期 typing 契约回归测试

覆盖 Mypy Phase 5 修复的两条运行时契约：
- ``PPTService._require_presentation`` 收窄访问器（未初始化时快速失败）
- ``_add_blank_slide`` 返回真实 Slide（不再退化为 object）
- 幻灯片尺寸 ``slide_width/slide_height`` 为 None 时的守卫回退
"""
import pytest
from unittest import mock

import pptx.presentation
from pptx import Presentation

from office_agent.ppt_agent.ppt_service import PPTService
from office_agent.ppt_agent.quality_checker import PPTQualityChecker


@pytest.mark.unit
class TestRequirePresentation:
    """收窄访问器：未初始化快速失败 / 初始化后返回 Presentation"""

    def test_raises_when_uninitialized(self):
        service = PPTService()
        assert service.prs is None
        with pytest.raises(RuntimeError, match="演示文稿尚未初始化"):
            service._require_presentation()

    def test_returns_presentation_after_set(self):
        service = PPTService()
        prs = Presentation()
        service.prs = prs
        assert service._require_presentation() is prs


@pytest.mark.unit
class TestAddBlankSlide:
    """_add_blank_slide 返回真实 Slide（带 shapes / background）"""

    def test_returns_typed_slide(self):
        service = PPTService()
        service.prs = Presentation()
        service._base_deck = True  # 跳过背景填充，避免依赖配色细节
        slide = service._add_blank_slide()
        assert slide is not None
        assert slide.shapes is not None
        assert slide.background is not None


@pytest.mark.unit
class TestSlideSizeNoneGuard:
    """模板未声明幻灯片尺寸时，宽度/高度计算回退到 16:9 默认值而非崩溃"""

    def test_check_handles_missing_slide_size(self, temp_dir):
        out = temp_dir / "min.pptx"
        Presentation().save(str(out))

        with mock.patch.object(
            pptx.presentation.Presentation, "slide_width",
            new_callable=mock.PropertyMock, return_value=None,
        ), mock.patch.object(
            pptx.presentation.Presentation, "slide_height",
            new_callable=mock.PropertyMock, return_value=None,
        ):
            report = PPTQualityChecker().check(str(out))

        assert report is not None
        assert report.slide_count == 0
