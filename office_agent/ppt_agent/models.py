"""
PPT Agent 数据模型
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SlideLayout(Enum):
    """幻灯片版式"""
    COVER = "cover"           # 封面
    TOC = "toc"               # 目录
    SECTION = "section"       # 章节过渡页
    CONTENT = "content"       # 标题+要点
    CONTENT_IMAGE = "content_image"  # 左文右图
    CONTENT_TWO_COL = "two_column"   # 两栏对比
    CONTENT_LIST = "content_list"    # 列表页
    DATA_CARDS = "data_cards"        # 数据卡片
    TIMELINE = "timeline"     # 时间线
    QUOTE = "quote"           # 引用/金句
    TABLE = "table"           # 表格页
    CHART = "chart"           # 图表页
    SUMMARY = "summary"       # 总结/致谢
    BLANK = "blank"           # 空白

    @classmethod
    def values(cls) -> tuple:
        """全部合法版式值（版式字符串的唯一权威来源）。"""
        return tuple(member.value for member in cls)

    @classmethod
    def is_valid(cls, value) -> bool:
        return value in cls._value2member_map_


class PPTTheme(Enum):
    """内置主题"""
    PROFESSIONAL = "professional"   # 商务蓝
    MINIMAL = "minimal"             # 简约黑白
    CREATIVE = "creative"           # 创意橙
    TECH = "tech"                   # 科技深色
    ACADEMIC = "academic"           # 学术
    NATURE = "nature"               # 自然绿

    @classmethod
    def values(cls) -> tuple:
        """全部合法主题值（主题字符串的唯一权威来源）。"""
        return tuple(member.value for member in cls)

    @classmethod
    def is_valid(cls, value) -> bool:
        return value in cls._value2member_map_


@dataclass
class ColorScheme:
    """配色方案"""
    primary: str = "#1F4E79"       # 主色
    secondary: str = "#2E75B6"     # 辅助色
    accent: str = "#FFC000"        # 强调色
    bg: str = "#FFFFFF"            # 背景色
    bg_dark: str = "#1F4E79"       # 深色背景
    text: str = "#333333"          # 正文色
    text_light: str = "#FFFFFF"    # 浅色文字
    text_muted: str = "#666666"    # 次要文字
    line: str = "#D9D9D9"          # 线条色


@dataclass
class FontScheme:
    """字体方案"""
    title_cn: str = "微软雅黑"
    title_en: str = "Calibri"
    body_cn: str = "微软雅黑"
    body_en: str = "Calibri"
    title_size: float = 28.0
    subtitle_size: float = 20.0
    body_size: float = 18.0
    caption_size: float = 14.0


@dataclass
class SlideContent:
    """单页幻灯片内容"""
    layout: str = "content"         # SlideLayout 值
    title: str = ""
    subtitle: str = ""
    bullets: list = field(default_factory=list)   # 要点列表
    body_text: str = ""             # 正文
    left_content: list = field(default_factory=list)   # 左栏
    right_content: list = field(default_factory=list)  # 右栏
    image_path: str = ""            # 图片路径
    image_alt: str = ""             # 图片说明
    image_prompt: str = ""          # 生图描述词（LLM 标记该页需要配图时提供）
    data: list = field(default_factory=list)       # 数据项 [(label, value, unit)]
    table_data: list = field(default_factory=list) # 表格数据 [[行1], [行2], ...] 第一行为表头
    table_header: bool = True                       # 第一行是否为表头
    chart_type: str = "bar"                         # 图表类型: bar/column/line/pie
    chart_title: str = ""                           # 图表标题
    chart_categories: list = field(default_factory=list)  # X轴类别
    chart_series: list = field(default_factory=list)      # 数据系列 [(name, [values])]
    quote_text: str = ""            # 引用文字
    quote_source: str = ""          # 引用来源
    timeline_items: list = field(default_factory=list)  # [(time, title, desc)]
    notes: str = ""                 # 备注
    body_font_size: Optional[float] = None  # 内容密度自适应字号
    page_number: int = 0

    def __post_init__(self):
        # 版式字符串的唯一校验点：内部构造出现拼写错误时快速失败。
        # 外部不可信输入（LLM JSON）必须在进入构造前完成规范化
        # （见 content_planner._parse_outline_json），此处不接受未知值。
        if not SlideLayout.is_valid(self.layout):
            raise ValueError(
                f"未知幻灯片版式: {self.layout!r}，合法值: {list(SlideLayout.values())}"
            )


@dataclass
class PPTOutline:
    """PPT 大纲"""
    title: str = ""
    subtitle: str = ""
    author: str = ""
    slides: list = field(default_factory=list)  # List[SlideContent]
    theme: str = "professional"
    color_scheme: Optional[ColorScheme] = None
    font_scheme: Optional[FontScheme] = None
    slide_width: float = 13.333     # 英寸
    slide_height: float = 7.5       # 英寸
    used_template: bool = False     # 是否因 LLM 失败/未配置而回退模板
    changes: list = field(default_factory=list)          # 生成过程中的变更记录
    _base_template_path: Optional[str] = None            # 基底模板文件路径

    def add_slide(self, slide: SlideContent):
        slide.page_number = len(self.slides) + 1
        self.slides.append(slide)


@dataclass
class PPTGenerationResult:
    """PPT 生成结果"""
    success: bool = False
    message: str = ""
    output_path: str = ""
    slide_count: int = 0
    changes: list = field(default_factory=list)
    quality_score: float = 100.0
    quality_issues: list = field(default_factory=list)
