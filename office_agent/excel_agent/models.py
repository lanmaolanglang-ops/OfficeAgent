"""
Excel Agent 数据模型
"""
from dataclasses import dataclass, field
from typing import Optional, List, Any
from enum import Enum

from ..models.schemas import ExcelTaskType
from ..quality.checker import IssueSeverity


def col_letter(index: int) -> str:
    """0-based 列索引转 Excel 列字母（0→A、25→Z、26→AA）。

    ColumnInfo.index 约定为 0-based；全包唯一的列字母换算入口。
    调用方禁止再自行 +1/-1——chart_generator 曾按 1-based 实现同名
    方法而 formula_generator 按 0-based 实现，跨类口径漂移由此产生。
    """
    if not isinstance(index, int) or index < 0:
        raise ValueError(f"列索引必须是非负整数: {index!r}")
    n = index + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters

# 兼容别名（清单 503）：Excel 任务子类型的唯一定义在
# office_agent.models.schemas.ExcelTaskType；旧桌面端/集成方可能
# import excel_agent.models.TaskType，路径与取值保持不变。
TaskType = ExcelTaskType


class ChartType(Enum):
    """图表类型"""
    BAR = "bar"
    COLUMN = "column"
    LINE = "line"
    PIE = "pie"
    AREA = "area"
    SCATTER = "scatter"
    DOUGHNUT = "doughnut"


class CellDataType(Enum):
    """单元格数据类型"""
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    FORMULA = "formula"
    BOOLEAN = "boolean"
    EMPTY = "empty"


class SemanticType(Enum):
    """字段语义类型"""
    DATE = "date"               # 日期字段
    AMOUNT = "amount"           # 金额字段
    QUANTITY = "quantity"       # 数量字段
    CATEGORY = "category"       # 分类字段
    METRIC = "metric"           # 指标字段
    ID = "id"                   # 标识字段
    NAME = "name"               # 名称字段
    TEXT = "text"               # 普通文本
    PERCENTAGE = "percentage"   # 百分比/比率
    BOOLEAN = "boolean"         # 是/否
    UNKNOWN = "unknown"


@dataclass
class ColumnInfo:
    """列信息"""
    name: str
    index: int                  # 0-based
    data_type: str = "text"     # text/number/date/boolean
    semantic_type: str = "unknown"  # date/amount/quantity/category/metric/id/name/text/percentage
    description: str = ""       # 字段说明
    sample_values: list = field(default_factory=list)
    unique_values: list = field(default_factory=list)  # 下游分组计算使用，最多保留20个
    null_count: int = 0
    unique_count: int = 0
    null_ratio: float = 0.0
    unique_ratio: float = 0.0
    min_value: Any = None
    max_value: Any = None
    avg_value: float = 0.0
    sum_value: float = 0.0
    is_primary_key: bool = False
    unit: str = ""              # 单位（元/个/%/人等）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "index": self.index,
            "type": self.data_type,
            "semantic_type": self.semantic_type,
            "description": self.description,
            "null_count": self.null_count,
            "unique_count": self.unique_count,
            "unique_values": self.unique_values,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "avg_value": self.avg_value,
            "sum_value": self.sum_value,
            "is_primary_key": self.is_primary_key,
            "unit": self.unit,
        }

    def to_schema_dict(self) -> dict:
        """生成 DataSchema 格式"""
        d = {
            "name": self.name,
            "type": self.data_type,
            "semantic_type": self.semantic_type,
        }
        if self.description:
            d["description"] = self.description
        if self.unit:
            d["unit"] = self.unit
        if self.is_primary_key:
            d["primary_key"] = True
        if self.sample_values:
            d["samples"] = self.sample_values[:3]
        return d


@dataclass
class SheetInfo:
    """工作表信息"""
    name: str
    row_count: int = 0
    col_count: int = 0
    columns: List[ColumnInfo] = field(default_factory=list)
    has_header: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "row_count": self.row_count,
            "col_count": self.col_count,
            "has_header": self.has_header,
            "columns": [c.to_dict() for c in self.columns],
        }


@dataclass
class DataProfile:
    """数据画像（DataAnalyzer 输出）"""
    file_path: str = ""
    sheets: List[SheetInfo] = field(default_factory=list)
    total_rows: int = 0
    total_sheets: int = 0
    summary: str = ""

    def get_sheet(self, name: str = None) -> Optional[SheetInfo]:
        if name is None:
            return self.sheets[0] if self.sheets else None
        for s in self.sheets:
            if s.name == name:
                return s
        return None


@dataclass
class FormulaSpec:
    """公式规格"""
    formula: str                # Excel 公式，如 "=SUM(A1:A10)"
    target_cell: str = ""       # 写入位置，如 "B11"
    description: str = ""
    category: str = ""          # sum/average/count/vlookup/if/etc.


@dataclass
class ChartSpec:
    """图表规格"""
    chart_type: str = "column"  # column/bar/line/pie/area/scatter/doughnut/radar/combo
    title: str = ""
    data_range: str = ""        # 如 "A1:D10"
    categories_range: str = ""  # X轴
    x_values_range: str = ""    # 散点图 X 值范围，如 "B2:B10"
    y_values_range: str = ""    # 散点图 Y 值范围，如 "E2:E10"
    x_title: str = ""
    y_title: str = ""
    position: str = ""          # 如 "E2" 锚点位置
    width: float = 15.0         # 厘米
    height: float = 10.0
    # 多系列支持
    series_names: List[str] = field(default_factory=list)
    series_ranges: List[str] = field(default_factory=list)  # 每个系列的数据范围
    secondary_y: bool = False   # 是否使用次坐标轴
    # 组合图
    combo_types: List[str] = field(default_factory=list)  # 如 ["column", "line"]
    combo_secondary: List[bool] = field(default_factory=list)  # 哪些系列用次轴
    # 样式
    style: int = 10
    show_data_labels: bool = False
    show_legend: bool = True
    legend_position: str = "bottom"  # bottom/top/right/left
    color_palette: List[str] = field(default_factory=list)
    # 堆积
    stacked: bool = False


@dataclass
class FormatSpec:
    """格式化规格"""
    range_str: str = ""         # 如 "A1:D1" 或 "A:D"
    font_name: str = ""
    font_size: int = 0
    bold: bool = False
    italic: bool = False
    font_color: str = ""
    bg_color: str = ""
    number_format: str = ""     # 如 "0.00%", "#,##0", "yyyy-mm-dd"
    alignment: str = ""         # left/center/right
    border: bool = False
    merge_cells: bool = False


@dataclass
class ExcelTask:
    """解析后的 Excel 任务"""
    task_type: TaskType = TaskType.UNKNOWN
    sheet_name: str = ""
    operations: list = field(default_factory=list)  # 操作列表
    formulas: List[FormulaSpec] = field(default_factory=list)
    charts: List[ChartSpec] = field(default_factory=list)
    formats: List[FormatSpec] = field(default_factory=list)
    output_sheet: str = ""
    description: str = ""


@dataclass
class ExcelResult:
    """Excel 处理结果"""
    success: bool = False
    message: str = ""
    output_path: str = ""
    sheet_count: int = 0
    changes: List[str] = field(default_factory=list)
    data_preview: Optional[dict] = None
    quality_score: float = 100.0
    quality_issues: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "output_path": self.output_path,
            "sheet_count": self.sheet_count,
            "changes": self.changes,
            "quality_score": self.quality_score,
        }


@dataclass
class ExcelQualityIssue:
    """Excel 质量问题"""
    sheet_name: str = ""
    issue_type: str = ""        # formula/format/data/chart/structure
    severity: str = IssueSeverity.WARNING.value
    message: str = ""
    cell_ref: str = ""
    fixable: bool = True
    fixed: bool = False

    def __post_init__(self):
        # 构造边界统一校验：枚举是唯一权威，未知 severity 不得静默漂移
        self.severity = IssueSeverity.normalize(self.severity)

    def to_dict(self) -> dict:
        return {
            "sheet": self.sheet_name,
            "type": self.issue_type,
            "severity": self.severity,
            "message": self.message,
            "cell": self.cell_ref,
            "fixable": self.fixable,
            "fixed": self.fixed,
        }


@dataclass
class DataRelation:
    """数据关系（表间关联）"""
    source_sheet: str
    source_column: str
    target_sheet: str
    target_column: str
    relation_type: str = "many_to_one"  # one_to_one / one_to_many / many_to_one
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "source": f"{self.source_sheet}.{self.source_column}",
            "target": f"{self.target_sheet}.{self.target_column}",
            "type": self.relation_type,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class SheetSchema:
    """工作表 Schema"""
    name: str
    row_count: int = 0
    col_count: int = 0
    columns: List[ColumnInfo] = field(default_factory=list)
    has_header: bool = True
    primary_key: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "sheet": self.name,
            "row_count": self.row_count,
            "col_count": self.col_count,
            "columns": [c.to_schema_dict() for c in self.columns],
        }

    def to_json(self, indent: int = 2) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass
class DataSchema:
    """
    数据 Schema - DataAnalyzer 的核心输出

    包含：所有工作表、字段类型、语义类型、数据关系
    """
    file_path: str = ""
    file_name: str = ""
    sheets: List[SheetSchema] = field(default_factory=list)
    relations: List[DataRelation] = field(default_factory=list)
    total_rows: int = 0
    total_sheets: int = 0
    summary: str = ""

    def get_sheet(self, name: str = None) -> Optional[SheetSchema]:
        if name is None:
            return self.sheets[0] if self.sheets else None
        for s in self.sheets:
            if s.name == name:
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "total_sheets": self.total_sheets,
            "total_rows": self.total_rows,
            "sheets": [s.to_dict() for s in self.sheets],
            "relations": [r.to_dict() for r in self.relations],
            "summary": self.summary,
        }

    def to_json(self, indent: int = 2) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, output_path: str):
        """保存 Schema 到 JSON 文件"""
        from pathlib import Path
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self.to_json())


# ==========================================
# Analysis Engine 数据模型
# ==========================================

class FindingType(Enum):
    """分析发现类型"""
    MAX = "max"                 # 最大值
    MIN = "min"                 # 最小值
    AVERAGE = "average"         # 平均值
    MEDIAN = "median"           # 中位数
    SUM = "sum"                 # 总计
    TREND_UP = "trend_up"       # 上升趋势
    TREND_DOWN = "trend_down"   # 下降趋势
    GROWTH = "growth"           # 增长
    DECLINE = "decline"         # 下降
    ANOMALY_HIGH = "anomaly_high"   # 异常高值
    ANOMALY_LOW = "anomaly_low"     # 异常低值
    OUTLIER = "outlier"         # 离群值
    RANK_TOP = "rank_top"       # Top排名
    RANK_BOTTOM = "rank_bottom" # Bottom排名
    CONCENTRATION = "concentration"  # 集中度
    CORRELATION = "correlation" # 相关性
    SUMMARY = "summary"         # 概要


class FindingSeverity(Enum):
    """发现重要程度"""
    INFO = "info"
    NOTABLE = "notable"         # 值得关注
    IMPORTANT = "important"     # 重要
    CRITICAL = "critical"       # 关键


@dataclass
class AnalysisFinding:
    """单条分析发现"""
    finding_type: str           # FindingType 值
    severity: str = "info"      # FindingSeverity 值
    title: str = ""             # 一句话标题，如"华东地区销售额最高"
    description: str = ""       # 详细描述
    sheet_name: str = ""
    column_name: str = ""
    dimension: str = ""         # 维度（如地区、产品）
    dimension_value: str = ""   # 维度值（如华东、产品A）
    value: Any = None           # 关键数值
    previous_value: Any = None  # 对比值
    change_rate: float = 0.0    # 变化率
    unit: str = ""
    rank: int = 0               # 排名
    total_count: int = 0        # 总数
    evidence: str = ""          # 证据/数据支撑
    suggestion: str = ""        # 建议

    def to_dict(self) -> dict:
        return {
            "type": self.finding_type,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "sheet": self.sheet_name,
            "column": self.column_name,
            "dimension": self.dimension,
            "dimension_value": self.dimension_value,
            "value": self.value,
            "previous_value": self.previous_value,
            "change_rate": round(self.change_rate, 4) if self.change_rate else 0,
            "unit": self.unit,
            "rank": self.rank,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
        }


@dataclass
class ColumnAnalysis:
    """单列分析结果"""
    column_name: str
    semantic_type: str = ""
    data_type: str = ""
    unit: str = ""
    count: int = 0
    sum_value: float = 0.0
    avg_value: float = 0.0
    median_value: float = 0.0
    max_value: Any = None
    max_label: str = ""         # 最大值对应的标签（如月份/地区）
    min_value: Any = None
    min_label: str = ""
    std_dev: float = 0.0
    cv: float = 0.0             # 变异系数
    growth_rate: float = 0.0    # 整体增长率（首末对比）
    trend: str = "stable"       # up/down/stable
    outliers: list = field(default_factory=list)
    top_values: list = field(default_factory=list)   # [(label, value)]
    bottom_values: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "column": self.column_name,
            "semantic_type": self.semantic_type,
            "unit": self.unit,
            "count": self.count,
            "sum": self.sum_value,
            "avg": self.avg_value,
            "median": self.median_value,
            "max": self.max_value,
            "max_label": self.max_label,
            "min": self.min_value,
            "min_label": self.min_label,
            "std_dev": self.std_dev,
            "growth_rate": round(self.growth_rate, 4),
            "trend": self.trend,
            "outliers": self.outliers,
            "top_values": self.top_values[:5],
            "bottom_values": self.bottom_values[:5],
        }


@dataclass
class GroupAnalysis:
    """分组分析结果"""
    dimension: str              # 分组维度列名
    metric: str                 # 指标列名
    groups: list = field(default_factory=list)  # [(group_value, sum, avg, count, share)]
    top_group: str = ""
    top_value: Any = None
    top_share: float = 0.0
    bottom_group: str = ""
    bottom_value: Any = None
    concentration: float = 0.0  # CR3 集中度

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "metric": self.metric,
            "groups": self.groups,
            "top_group": self.top_group,
            "top_value": self.top_value,
            "top_share": round(self.top_share, 4),
            "bottom_group": self.bottom_group,
            "bottom_value": self.bottom_value,
            "concentration": round(self.concentration, 4),
        }


@dataclass
class TrendAnalysis:
    """趋势分析结果"""
    column_name: str
    time_column: str = ""
    trend: str = "stable"       # up/down/stable/fluctuating
    growth_rate: float = 0.0
    avg_growth_rate: float = 0.0   # 平均环比增长率
    max_growth_period: str = ""
    max_growth_rate: float = 0.0
    max_decline_period: str = ""
    max_decline_rate: float = 0.0
    period_count: int = 0
    periods: list = field(default_factory=list)  # [(period, value, change_rate)]

    def to_dict(self) -> dict:
        return {
            "column": self.column_name,
            "time_column": self.time_column,
            "trend": self.trend,
            "growth_rate": round(self.growth_rate, 4),
            "avg_growth_rate": round(self.avg_growth_rate, 4),
            "max_growth_period": self.max_growth_period,
            "max_growth_rate": round(self.max_growth_rate, 4),
            "max_decline_period": self.max_decline_period,
            "max_decline_rate": round(self.max_decline_rate, 4),
            "periods": self.periods,
        }


@dataclass
class AnalysisReport:
    """分析报告"""
    file_path: str = ""
    file_name: str = ""
    sheet_name: str = ""
    total_rows: int = 0
    total_columns: int = 0
    analysis_time: str = ""

    # 各维度分析
    column_analyses: List[ColumnAnalysis] = field(default_factory=list)
    group_analyses: List[GroupAnalysis] = field(default_factory=list)
    trend_analyses: List[TrendAnalysis] = field(default_factory=list)
    findings: List[AnalysisFinding] = field(default_factory=list)

    # 报告文本
    summary: str = ""           # 概要
    key_findings: str = ""      # 关键发现
    detailed_analysis: str = "" # 详细分析
    recommendations: str = ""   # 建议
    sheet_reports: List["AnalysisReport"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "sheet": self.sheet_name,
            "total_rows": self.total_rows,
            "total_columns": self.total_columns,
            "analysis_time": self.analysis_time,
            "summary": self.summary,
            "key_findings": [f.to_dict() for f in self.findings[:20]],
            "column_analyses": [c.to_dict() for c in self.column_analyses],
            "group_analyses": [g.to_dict() for g in self.group_analyses],
            "trend_analyses": [t.to_dict() for t in self.trend_analyses],
            "recommendations": self.recommendations,
            "sheet_reports": [report.to_dict() for report in self.sheet_reports],
        }

    def to_json(self, indent: int = 2) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_text(self) -> str:
        """生成可读的文本报告"""
        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"  数据分析报告：{self.file_name}")
        lines.append(f"  工作表：{self.sheet_name}  |  {self.total_rows}行 × {self.total_columns}列")
        lines.append(f"{'='*60}")

        if self.summary:
            lines.append("")
            lines.append("【概要】")
            lines.append(self.summary)

        if self.findings:
            lines.append("")
            lines.append("【关键发现】")
            for i, f in enumerate(self.findings[:15], 1):
                icon = {"info": "ℹ️", "notable": "📌", "important": "⭐", "critical": "🔴"}.get(f.severity, "•")
                lines.append(f"  {icon} {f.title}")
                if f.description:
                    lines.append(f"    {f.description}")

        if self.trend_analyses:
            lines.append("")
            lines.append("【趋势分析】")
            for t in self.trend_analyses:
                trend_icon = {"up": "📈", "down": "📉", "stable": "➡️", "fluctuating": "📊"}.get(t.trend, "•")
                lines.append(f"  {trend_icon} {t.column_name}: {t.trend}")
                if t.avg_growth_rate:
                    lines.append(f"    平均环比: {t.avg_growth_rate:+.1%}")
                if t.max_growth_period:
                    lines.append(f"    最大增幅: {t.max_growth_period} ({t.max_growth_rate:+.1%})")
                if t.max_decline_period:
                    lines.append(f"    最大降幅: {t.max_decline_period} ({t.max_decline_rate:+.1%})")

        if self.group_analyses:
            lines.append("")
            lines.append("【分组分析】")
            for g in self.group_analyses:
                lines.append(f"  按{g.dimension}分析{g.metric}:")
                lines.append(f"    最高: {g.top_group} ({g.top_value:,.0f})")
                lines.append(f"    最低: {g.bottom_group} ({g.bottom_value:,.0f})")
                if g.concentration:
                    lines.append(f"    Top3集中度: {g.concentration:.1%}")

        if self.column_analyses:
            lines.append("")
            lines.append("【指标统计】")
            for c in self.column_analyses:
                if c.semantic_type in ("amount", "quantity", "metric"):
                    lines.append(f"  {c.column_name}:")
                    lines.append(f"    合计: {c.sum_value:,.2f} {c.unit}")
                    lines.append(f"    平均: {c.avg_value:,.2f} {c.unit}")
                    lines.append(f"    最大: {c.max_value:,.2f} ({c.max_label})")
                    lines.append(f"    最小: {c.min_value:,.2f} ({c.min_label})")

        if self.recommendations:
            lines.append("")
            lines.append("【建议】")
            lines.append(self.recommendations)

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def save(self, output_path: str):
        """保存报告"""
        from pathlib import Path
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        if output_path.endswith(".json"):
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(self.to_json())
        else:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(self.to_text())
