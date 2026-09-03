"""
数据模型定义
定义 Agent 系统中使用的所有数据结构
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from pathlib import Path


class TaskType(Enum):
    """任务类型枚举"""
    WORD = "word"
    PPT = "ppt"
    EXCEL = "excel"
    PDF = "pdf"
    TEXT = "text"
    IMAGE = "image"
    UNKNOWN = "unknown"


class WordTaskType(Enum):
    """Word 任务子类型"""
    FORMAT = "format"           # 格式调整
    LAYOUT = "layout"           # 自动排版
    PAPER = "paper"             # 论文排版
    REPORT = "report"           # 报告排版
    TEMPLATE = "template"       # 模板套用
    HEADING = "heading"         # 标题层级整理
    TABLE = "table"             # 表格规范化
    GENERATE = "generate"       # 文档生成


class PPTTaskType(Enum):
    """PPT 任务子类型"""
    THEME = "theme"             # 根据主题生成
    TEXT = "text"               # 根据文字生成
    TEMPLATE = "template"       # 根据模板生成
    OPTIMIZE = "optimize"       # 内容优化
    STRUCTURE = "structure"     # 结构设计


class ExcelTaskType(Enum):
    """Excel 任务子类型（全仓唯一定义，清单 503）。

    取原 schemas.ExcelTaskType 与 excel_agent.models.TaskType 的并集；
    excel_agent 侧保留 `TaskType = ExcelTaskType` 兼容别名，
    旧桌面端/集成方的 import 路径与既有取值全部不变。
    """
    READ = "read"               # 读取/查看数据
    CALCULATE = "calculate"     # 数据计算
    ANALYZE = "analyze"         # 数据分析
    FORMULA = "formula"         # 自动生成公式
    FORMAT = "format"           # 格式化
    CHART = "chart"             # 图表生成
    PIVOT = "pivot"             # 数据透视
    FILTER = "filter"           # 筛选/排序
    MERGE = "merge"             # 合并/汇总
    CREATE = "create"           # 表格创建
    CLEAN = "clean"             # 数据整理
    TEMPLATE = "template"       # 应用模板
    UNKNOWN = "unknown"


class Alignment(Enum):
    """对齐方式"""
    LEFT = "left"
    RIGHT = "right"
    CENTER = "center"
    JUSTIFY = "justify"


@dataclass
class FileInfo:
    """文件信息"""
    path: str
    filename: str = ""
    file_type: TaskType = TaskType.UNKNOWN
    size: int = 0
    
    def __post_init__(self):
        if not self.filename:
            self.filename = Path(self.path).name
        ext = Path(self.path).suffix.lower()
        type_map = {
            ".docx": TaskType.WORD,
            ".pptx": TaskType.PPT,
            ".xlsx": TaskType.EXCEL,
            ".csv": TaskType.EXCEL,
            ".pdf": TaskType.PDF,
            ".txt": TaskType.TEXT,
            ".md": TaskType.TEXT,
            ".png": TaskType.IMAGE,
            ".jpg": TaskType.IMAGE,
            ".jpeg": TaskType.IMAGE,
        }
        self.file_type = type_map.get(ext, TaskType.UNKNOWN)


@dataclass
class FontConfig:
    """字体配置"""
    cn_font: str = "宋体"           # 中文字体
    en_font: str = "Times New Roman"  # 英文字体
    size: float = 12.0              # 字号（磅）
    bold: bool = False              # 加粗
    italic: bool = False            # 斜体
    underline: bool = False         # 下划线
    color: Optional[str] = None     # 颜色（十六进制）


@dataclass
class ParagraphConfig:
    """段落配置"""
    alignment: Alignment = Alignment.JUSTIFY  # 对齐方式
    line_spacing: float = 1.25      # 行距倍数
    line_spacing_rule: str = "multiple"  # multiple/exactly（固定磅值）
    space_before: float = 0.0       # 段前距（磅）
    space_after: float = 0.0        # 段后距（磅）
    first_line_indent: float = 0.0  # 首行缩进（磅）
    first_line_indent_chars: float = 0.0  # 首行缩进（字符，按当前字号换算）
    hanging_indent: float = 0.0     # 悬挂缩进（磅）


@dataclass
class HeadingConfig:
    """标题配置"""
    level: int = 1
    font: FontConfig = field(default_factory=FontConfig)
    paragraph: ParagraphConfig = field(default_factory=ParagraphConfig)
    numbering: bool = True          # 是否自动编号


@dataclass
class TableConfig:
    """表格配置"""
    three_line: bool = True          # 是否应用三线表
    top_border: float = 1.5         # 顶部边框（磅）
    middle_border: float = 0.75     # 中间边框（磅）
    bottom_border: float = 1.5      # 底部边框（磅）
    show_left_right: bool = False   # 是否显示左右边框
    auto_number: bool = True        # 自动编号
    caption_font_size: float = 10.5  # 表名字号
    caption_alignment: Alignment = Alignment.CENTER  # 表名对齐


@dataclass
class PageSetupConfig:
    """页面设置配置"""
    margin_top: float = 2.54       # 上边距（cm）
    margin_bottom: float = 2.54    # 下边距（cm）
    margin_left: float = 3.17      # 左边距（cm）
    margin_right: float = 3.17     # 右边距（cm）
    page_width: float = 21.0       # 纸张宽度（cm）
    page_height: float = 29.7      # 纸张高度（cm）
    header_distance: float = 1.5   # 页眉距离（cm）
    footer_distance: float = 1.75  # 页脚距离（cm）


@dataclass
class FormatConfig:
    """完整格式配置"""
    body_font: FontConfig = field(default_factory=lambda: FontConfig(
        cn_font="宋体",
        en_font="Times New Roman",
        size=12.0,  # 小四
        bold=False,
    ))
    body_paragraph: ParagraphConfig = field(default_factory=lambda: ParagraphConfig(
        alignment=Alignment.JUSTIFY,
        line_spacing=1.25,
        first_line_indent_chars=2.0,
    ))
    headings: dict[int, HeadingConfig] = field(default_factory=dict)
    table_config: TableConfig = field(default_factory=TableConfig)
    page_setup: PageSetupConfig = field(default_factory=PageSetupConfig)
    
    def __post_init__(self):
        if not self.headings:
            self.headings = self._default_headings()
    
    @staticmethod
    def _default_headings() -> dict[int, HeadingConfig]:
        """默认标题配置"""
        return {
            1: HeadingConfig(
                level=1,
                font=FontConfig(cn_font="黑体", en_font="Times New Roman", size=15.0, bold=True),  # 小三
                paragraph=ParagraphConfig(
                    alignment=Alignment.LEFT,
                    line_spacing=1.0,
                    space_before=6.0,
                    space_after=6.0,
                ),
            ),
            2: HeadingConfig(
                level=2,
                font=FontConfig(cn_font="黑体", en_font="Times New Roman", size=14.0, bold=True),  # 四号
                paragraph=ParagraphConfig(
                    alignment=Alignment.LEFT,
                    line_spacing=1.0,
                    space_before=6.0,
                    space_after=6.0,
                ),
            ),
            3: HeadingConfig(
                level=3,
                font=FontConfig(cn_font="黑体", en_font="Times New Roman", size=12.0, bold=True),  # 小四
                paragraph=ParagraphConfig(
                    alignment=Alignment.LEFT,
                    line_spacing=1.0,
                    space_before=6.0,
                    space_after=6.0,
                ),
            ),
            4: HeadingConfig(
                level=4,
                font=FontConfig(cn_font="宋体", en_font="Times New Roman", size=12.0, bold=True),
                paragraph=ParagraphConfig(
                    alignment=Alignment.LEFT,
                    line_spacing=1.0,
                    space_before=6.0,
                    space_after=6.0,
                ),
            ),
        }


@dataclass
class WordProcessParams:
    """Word 处理参数"""
    task_type: WordTaskType = WordTaskType.LAYOUT
    format_config: FormatConfig = field(default_factory=FormatConfig)
    template_path: Optional[str] = None
    output_path: Optional[str] = None
    custom_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class PPTProcessParams:
    """PPT 处理参数"""
    task_type: PPTTaskType = PPTTaskType.THEME
    theme: str = ""
    content: str = ""
    template_path: Optional[str] = None
    output_path: Optional[str] = None
    slide_count: int = 10
    style: str = "professional"  # professional/minimal/creative
    custom_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExcelProcessParams:
    """Excel 处理参数"""
    task_type: ExcelTaskType = ExcelTaskType.CALCULATE
    formula_description: str = ""
    data_range: str = ""
    output_path: Optional[str] = None
    chart_type: str = ""  # bar/line/pie/column
    custom_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessResult:
    """处理结果"""
    success: bool
    message: str
    output_path: Optional[str] = None
    changes: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    """Agent 响应"""
    task_type: TaskType
    understood: bool
    intent: str
    params: Any
    result: Optional[ProcessResult] = None
    questions: list[str] = field(default_factory=list)
