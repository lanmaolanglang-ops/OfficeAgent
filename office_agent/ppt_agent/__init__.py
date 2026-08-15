"""
PPT Agent - 智能 PPT 生成模块

架构：
用户需求 → PPTOrchestrator → ContentPlanner → SlideDesigner → PPTService → .pptx
                                  ↓
                           TemplateAnalyzer（可选）
                                  ↓
                           PPTQualityChecker（自动检查）
"""

from .ppt_orchestrator import PPTOrchestrator
from .content_planner import ContentPlanner
from .slide_planner import SlidePlanner, SlidePlan, SlidePlanItem
from .template_analyzer import (
    TemplateAnalyzer,
    TemplateConfig,
    TemplateInfo,
    MasterInfo,
    LayoutInfo,
    PlaceholderInfo,
    ThemeColorInfo,
    ThemeFontInfo,
    analyze_template,
)
from .slide_designer import SlideDesigner, StylePresets
from .ppt_service import PPTService, THEME_COLORS
from .quality_checker import (
    PPTQualityChecker, PPTQualityReport, PPTQualityIssue,
    check_ppt, check_and_fix_outline,
)
from .models import (
    PPTOutline,
    SlideContent,
    ColorScheme,
    FontScheme,
    SlideLayout,
    PPTTheme,
    PPTGenerationResult,
)

__all__ = [
    "PPTOrchestrator",
    "ContentPlanner",
    "SlidePlanner",
    "SlidePlan",
    "SlidePlanItem",
    "TemplateAnalyzer",
    "TemplateConfig",
    "TemplateInfo",
    "MasterInfo",
    "LayoutInfo",
    "PlaceholderInfo",
    "ThemeColorInfo",
    "ThemeFontInfo",
    "analyze_template",
    "SlideDesigner",
    "StylePresets",
    "PPTService",
    "THEME_COLORS",
    "PPTQualityChecker",
    "PPTQualityReport",
    "PPTQualityIssue",
    "check_ppt",
    "check_and_fix_outline",
    "PPTOutline",
    "SlideContent",
    "ColorScheme",
    "FontScheme",
    "SlideLayout",
    "PPTTheme",
    "PPTGenerationResult",
]
