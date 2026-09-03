"""PPT 双风格系统合一的专项测试（清单 502）。

StylePresets 与 THEME_COLORS 曾各存一份相同色值（business 即
professional 的复制）；合一后配色唯一定义在 THEME_COLORS，
StylePresets 只补充字体方案。
"""
import inspect

from office_agent.ppt_agent import slide_designer
from office_agent.ppt_agent.ppt_service import THEME_COLORS
from office_agent.ppt_agent.slide_designer import StylePresets


class TestColorsSingleSource:
    def test_presets_return_theme_color_instances(self):
        mapping = {
            "business": "professional",
            "minimal": "minimal",
            "tech": "tech",
            "creative": "creative",
            "academic": "academic",
        }
        for preset, theme_key in mapping.items():
            colors, _fonts = getattr(StylePresets, preset)()
            assert colors is THEME_COLORS[theme_key], preset

    def test_no_hex_colors_hardcoded_in_presets(self):
        """StylePresets 内不得再出现色值字面量（源码守卫）。"""
        src = inspect.getsource(slide_designer.StylePresets)
        assert "primary=" not in src
        assert "#" not in src.replace("# ", "")  # 允许注释，禁止色值

    def test_font_schemes_preserved(self):
        """合一不得丢失各预设的字体差异。"""
        assert StylePresets.minimal()[1].title_cn == "思源黑体"
        assert StylePresets.academic()[1].title_cn == "黑体"
        assert StylePresets.academic()[1].body_cn == "宋体"
        assert StylePresets.creative()[1].title_size == 30
        assert StylePresets.business()[1].title_size == 28

    def test_preset_api_surface_unchanged(self):
        for name in ("business", "minimal", "tech", "creative", "academic"):
            colors, fonts = getattr(StylePresets, name)()
            assert colors.primary.startswith("#")
            assert fonts.body_size > 0
