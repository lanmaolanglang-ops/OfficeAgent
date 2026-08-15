"""
Slide Designer - 幻灯片视觉设计器

负责：
1. 根据内容类型自动选择最佳版式
2. 布局计算与内容适配
3. 配色方案与字体层级管理
4. 内容溢出处理（自动调整字号/分页）
5. 视觉一致性保障
"""
from typing import Optional, List

from .models import PPTOutline, SlideContent, ColorScheme, FontScheme, SlideLayout
from .ppt_service import THEME_COLORS, DEFAULT_FONTS


class SlideDesigner:
    """
    幻灯片设计器

    用法:
        designer = SlideDesigner(style="professional")
        designed = designer.design(outline)
    """

    # 每页最大要点数（超过则建议分页或缩小字号）
    MAX_BULLETS_PER_SLIDE = 6
    # 每个要点最大字数
    MAX_CHARS_PER_BULLET = 50
    # 每页最大文字量
    MAX_CHARS_PER_SLIDE = 300

    def __init__(self, style: str = "professional",
                 color_scheme: Optional[ColorScheme] = None,
                 font_scheme: Optional[FontScheme] = None):
        self.style = style
        self.colors = color_scheme or THEME_COLORS.get(style, THEME_COLORS["professional"])
        self.fonts = font_scheme or DEFAULT_FONTS

    def design(self, outline: PPTOutline) -> PPTOutline:
        """
        对大纲进行视觉设计优化

        - 自动为内容选择合适的版式
        - 检查内容溢出
        - 统一配色和字体
        - 添加页码和装饰
        """
        # 应用配色和字体
        outline.color_scheme = self.colors
        outline.font_scheme = self.fonts
        outline.theme = self.style

        # 优化每页
        designed_slides = []
        for slide in outline.slides:
            # 自动选择版式（如果是默认 content 且内容特征明显）
            if slide.layout == "content":
                slide.layout = self._auto_select_layout(slide)

            # 内容适配
            slide = self._fit_content(slide)

            designed_slides.append(slide)

        outline.slides = designed_slides

        # 重新编号
        for i, s in enumerate(outline.slides):
            s.page_number = i + 1

        return outline

    def _auto_select_layout(self, slide: SlideContent) -> str:
        """根据内容特征自动选择版式"""
        bullets = slide.bullets or []
        data = slide.data or []
        timeline = slide.timeline_items or []

        # 有表格数据 → 表格页
        if slide.table_data:
            return "table"

        # 有图表数据 → 图表页
        if slide.chart_categories and slide.chart_series:
            return "chart"

        # 有数据 → 数据卡片
        if data:
            return "data_cards"

        # 有时间线 → 时间线
        if timeline:
            return "timeline"

        # 有引用 → 引用页
        if slide.quote_text:
            return "quote"

        # 有图片 → 图文页
        if slide.image_path:
            return "content_image"

        # 左右都有内容 → 两栏
        if slide.left_content and slide.right_content:
            return "two_column"

        # 要点较多 → 列表页
        if len(bullets) >= 5:
            return "content_list"

        # 要点都是短文本（适合卡片展示）
        if bullets and all(len(b) < 20 for b in bullets if isinstance(b, str)):
            if len(bullets) >= 4:
                return "content_list"

        return "content"

    def _fit_content(self, slide: SlideContent) -> SlideContent:
        """内容适配：处理溢出"""
        bullets = slide.bullets or []

        # 截断过长的要点
        fitted_bullets = []
        for b in bullets:
            if isinstance(b, str):
                if len(b) > self.MAX_CHARS_PER_BULLET:
                    b = b[:self.MAX_CHARS_PER_BULLET - 3] + "..."
                fitted_bullets.append(b)
            else:
                fitted_bullets.append(b)

        slide.bullets = fitted_bullets[:self.MAX_BULLETS_PER_SLIDE]

        # 如果要点过多，保留主要的
        if len(bullets) > self.MAX_BULLETS_PER_SLIDE:
            slide.notes = (slide.notes or "") + f"\n注：原{len(bullets)}个要点，已精简为{self.MAX_BULLETS_PER_SLIDE}个"

        return slide

    def estimate_content_density(self, slide: SlideContent) -> str:
        """估算内容密度"""
        total_chars = 0
        total_chars += len(slide.title or "")
        total_chars += len(slide.subtitle or "")
        total_chars += sum(len(b) for b in (slide.bullets or []) if isinstance(b, str))
        total_chars += len(slide.body_text or "")

        if total_chars > self.MAX_CHARS_PER_SLIDE:
            return "high"
        elif total_chars > 150:
            return "medium"
        else:
            return "low"

    def suggest_font_size(self, slide: SlideContent) -> int:
        """根据内容密度建议字号"""
        density = self.estimate_content_density(slide)
        if density == "high":
            return max(14, self.fonts.body_size - 4)
        elif density == "medium":
            return self.fonts.body_size
        else:
            return self.fonts.body_size + 2

    def get_layout_description(self, layout: str) -> str:
        """获取版式说明"""
        descriptions = {
            "cover": "封面页 - 大标题居中，深色背景，装饰色块",
            "toc": "目录页 - 序号+标题列表，装饰线",
            "section": "章节过渡页 - 半屏深色，大号章节号",
            "content": "内容页 - 顶部标题栏，要点列表",
            "content_image": "图文页 - 左侧要点，右侧图片",
            "two_column": "两栏对比 - 左右分栏卡片",
            "content_list": "列表页 - 带序号圆点的列表",
            "data_cards": "数据卡片 - 四个彩色数据展示卡",
            "table": "表格页 - 标题+数据表格，表头主色背景",
            "chart": "图表页 - 标题+柱状/折线/饼图",
            "timeline": "时间线 - 横向时间轴+节点",
            "quote": "引用页 - 深色背景，大引号，金句",
            "summary": "总结页 - 深色背景，感谢文字",
            "blank": "空白页 - 仅标题",
        }
        return descriptions.get(layout, "标准内容页")


class StylePresets:
    """预设风格快速切换"""

    @staticmethod
    def business() -> tuple:
        """商务风格"""
        return (
            ColorScheme(
                primary="#1F4E79", secondary="#2E75B6", accent="#FFC000",
                bg="#FFFFFF", bg_dark="#1F4E79",
                text="#333333", text_light="#FFFFFF", text_muted="#666666",
                line="#D9D9D9",
            ),
            FontScheme(title_cn="微软雅黑", body_cn="微软雅黑", title_size=28, body_size=18),
        )

    @staticmethod
    def minimal() -> tuple:
        """简约风格"""
        return (
            ColorScheme(
                primary="#000000", secondary="#595959", accent="#C00000",
                bg="#FFFFFF", bg_dark="#000000",
                text="#000000", text_light="#FFFFFF", text_muted="#808080",
                line="#E0E0E0",
            ),
            FontScheme(title_cn="思源黑体", body_cn="思源黑体", title_size=26, body_size=16),
        )

    @staticmethod
    def tech() -> tuple:
        """科技风格"""
        return (
            ColorScheme(
                primary="#0066CC", secondary="#00B0F0", accent="#00FF88",
                bg="#0A1628", bg_dark="#0066CC",
                text="#E0E0E0", text_light="#FFFFFF", text_muted="#999999",
                line="#1A3050",
            ),
            FontScheme(title_cn="微软雅黑", body_cn="微软雅黑", title_size=28, body_size=18),
        )

    @staticmethod
    def creative() -> tuple:
        """创意风格"""
        return (
            ColorScheme(
                primary="#ED7D31", secondary="#FFC000", accent="#70AD47",
                bg="#FFFFFF", bg_dark="#ED7D31",
                text="#333333", text_light="#FFFFFF", text_muted="#666666",
                line="#F0D5B8",
            ),
            FontScheme(title_cn="微软雅黑", body_cn="微软雅黑", title_size=30, body_size=18),
        )

    @staticmethod
    def academic() -> tuple:
        """学术风格"""
        return (
            ColorScheme(
                primary="#2E4057", secondary="#048A81", accent="#D1495B",
                bg="#FFFFFF", bg_dark="#2E4057",
                text="#2E4057", text_light="#FFFFFF", text_muted="#6B7B8C",
                line="#D0D5DD",
            ),
            FontScheme(title_cn="黑体", body_cn="宋体", title_size=24, body_size=18),
        )
