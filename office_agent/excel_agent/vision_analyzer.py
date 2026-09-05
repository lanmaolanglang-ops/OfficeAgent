"""
Excel Vision Analyzer - Excel 截图智能分析器

通过视觉模型识别 Excel 截图，理解：
- 表格类型（销售报表/财务/库存/人事等）
- 表名/标题
- 字段/列名及其语义类型
- 数据区域和示例数据
- 自动生成分析任务建议
"""
import os
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from enum import Enum

from ..vision_gateway import (
    VisionGateway, VisionRequest, VisionResponse,
    ImageInput, VisionTaskType,
)


class TableType(Enum):
    """识别的表格类型"""
    SALES = "sales"               # 销售报表
    FINANCE = "finance"           # 财务报表
    INVENTORY = "inventory"       # 库存表
    HR = "hr"                     # 人事/员工表
    PROJECT = "project"           # 项目表
    CUSTOMER = "customer"         # 客户表
    MARKETING = "marketing"       # 营销表
    PURCHASE = "purchase"         # 采购表
    LOGISTICS = "logistics"       # 物流表
    BUDGET = "budget"             # 预算表
    SURVEY = "survey"             # 调查/问卷
    SCHEDULE = "schedule"         # 日程/排班
    SCORE = "score"               # 成绩/评分
    LEDGER = "ledger"             # 台账/流水
    UNKNOWN = "unknown"


class ColumnSemanticType(Enum):
    """列语义类型"""
    DATE = "date"                 # 日期
    TIME = "time"                 # 时间
    DATETIME = "datetime"         # 日期时间
    AMOUNT = "amount"             # 金额
    CURRENCY = "currency"         # 货币
    QUANTITY = "quantity"         # 数量
    PRICE = "price"               # 单价
    PERCENTAGE = "percentage"     # 百分比
    RATIO = "ratio"               # 比率
    COUNT = "count"               # 计数
    NAME = "name"                 # 名称
    CATEGORY = "category"         # 分类/类别
    PRODUCT = "product"           # 产品
    REGION = "region"             # 地区
    DEPARTMENT = "department"     # 部门
    PERSON = "person"             # 人员
    ID = "id"                     # 编号/ID
    PHONE = "phone"               # 电话
    EMAIL = "email"               # 邮箱
    ADDRESS = "address"           # 地址
    STATUS = "status"             # 状态
    BOOLEAN = "boolean"           # 是/否
    TEXT = "text"                 # 普通文本
    NUMBER = "number"             # 数字
    UNKNOWN = "unknown"


class AnalysisType(Enum):
    """建议的分析类型"""
    SUMMARY = "summary"           # 汇总统计
    TREND = "trend"               # 趋势分析
    COMPARISON = "comparison"     # 对比分析
    RANKING = "ranking"           # 排名分析
    GROUPBY = "groupby"           # 分组分析
    PIVOT = "pivot"               # 透视分析
    GROWTH = "growth"             # 增长率
    PROPORTION = "proportion"     # 占比分析
    ANOMALY = "anomaly"           # 异常检测
    CORRELATION = "correlation"   # 相关性
    FORECAST = "forecast"         # 预测
    TOP_N = "top_n"               # TOP N
    DISTRIBUTION = "distribution" # 分布分析


class ChartSuggestion(Enum):
    """推荐图表类型"""
    BAR = "bar"                   # 柱状图
    LINE = "line"                 # 折线图
    PIE = "pie"                   # 饼图
    AREA = "area"                 # 面积图
    SCATTER = "scatter"           # 散点图
    COLUMN = "column"             # 条形图
    COMBO = "combo"               # 组合图
    HEATMAP = "heatmap"           # 热力图
    TABLE = "table"               # 表格


@dataclass
class RecognizedColumn:
    """识别的列/字段"""
    name: str = ""                # 列名
    index: int = 0                # 列序号（从0开始）
    column_letter: str = ""       # Excel列字母（A, B, C...）
    semantic_type: str = "unknown"  # ColumnSemanticType
    data_type: str = "text"       # text/number/date/boolean
    sample_values: List[str] = field(default_factory=list)
    description: str = ""         # 字段含义描述
    unit: str = ""                # 单位（元/个/%等）
    is_dimension: bool = False    # 是否维度字段
    is_metric: bool = False       # 是否指标字段

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RecognizedTable:
    """识别的表格"""
    title: str = ""               # 表名/标题
    table_type: str = "unknown"   # TableType
    table_type_confidence: float = 0.0
    description: str = ""         # 表格描述
    columns: List[RecognizedColumn] = field(default_factory=list)
    data_row_count: int = 0       # 数据行数（估算）
    header_row: int = 1           # 表头行号
    data_start_row: int = 2       # 数据起始行
    data_end_row: int = 0         # 数据结束行
    sample_data: List[List[str]] = field(default_factory=list)  # 前几行示例
    has_merged_cells: bool = False
    has_total_row: bool = False   # 是否有合计行
    notes: str = ""               # 备注（如"含公式""有条件格式"等）

    def get_dimension_columns(self) -> List[RecognizedColumn]:
        return [c for c in self.columns if c.is_dimension]

    def get_metric_columns(self) -> List[RecognizedColumn]:
        return [c for c in self.columns if c.is_metric]

    def get_date_columns(self) -> List[RecognizedColumn]:
        return [c for c in self.columns if c.semantic_type in ("date", "time", "datetime")]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dimension_columns"] = [c.name for c in self.get_dimension_columns()]
        d["metric_columns"] = [c.name for c in self.get_metric_columns()]
        return d


@dataclass
class SuggestedAnalysis:
    """建议的分析任务"""
    analysis_type: str = ""       # AnalysisType
    title: str = ""               # 分析标题
    description: str = ""         # 分析描述
    columns_involved: List[str] = field(default_factory=list)
    chart_suggestion: str = ""    # ChartSuggestion
    priority: int = 3             # 1-5，1最高
    formula_hint: str = ""        # 公式提示（如SUMIFS）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExcelVisionResult:
    """Excel 截图分析结果"""
    success: bool = False
    error: str = ""
    image_path: str = ""
    table: RecognizedTable = field(default_factory=RecognizedTable)
    suggested_analyses: List[SuggestedAnalysis] = field(default_factory=list)
    overall_summary: str = ""
    raw_response: str = ""
    model_used: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "error": self.error,
            "image_path": self.image_path,
            "table": self.table.to_dict(),
            "suggested_analyses": [a.to_dict() for a in self.suggested_analyses],
            "overall_summary": self.overall_summary,
            "model_used": self.model_used,
        }

    def to_text(self) -> str:
        """生成可读文本"""
        lines = []
        lines.append("=" * 60)
        lines.append("  Excel 截图智能分析结果")
        lines.append("=" * 60)

        if not self.success:
            lines.append(f"错误: {self.error}")
            return "\n".join(lines)

        t = self.table

        # 表格识别
        lines.append(f"\n【表格识别】")
        type_names = {
            "sales": "销售报表", "finance": "财务报表", "inventory": "库存表",
            "hr": "人事表", "project": "项目表", "customer": "客户表",
            "marketing": "营销表", "purchase": "采购表", "logistics": "物流表",
            "budget": "预算表", "survey": "调查问卷", "schedule": "日程排班",
            "score": "成绩评分", "ledger": "台账流水",
        }
        type_name = type_names.get(t.table_type, t.table_type)
        lines.append(f"  表名: {t.title or '(未识别)'}")
        lines.append(f"  类型: {type_name} (置信度: {t.table_type_confidence:.0%})")
        if t.description:
            lines.append(f"  描述: {t.description}")
        lines.append(f"  数据行: 约 {t.data_row_count} 行")
        lines.append(f"  字段数: {len(t.columns)} 个")

        if t.has_total_row:
            lines.append(f"  备注: 含合计行")
        if t.has_merged_cells:
            lines.append(f"  备注: 含合并单元格")

        # 字段详情
        lines.append(f"\n【字段识别】")
        sem_names = {
            "date": "日期", "time": "时间", "datetime": "日期时间",
            "amount": "金额", "currency": "货币", "quantity": "数量",
            "price": "单价", "percentage": "百分比", "ratio": "比率",
            "count": "计数", "name": "名称", "category": "分类",
            "product": "产品", "region": "地区", "department": "部门",
            "person": "人员", "id": "编号", "status": "状态",
            "boolean": "是/否", "text": "文本", "number": "数字",
        }
        for col in t.columns:
            sem = sem_names.get(col.semantic_type, col.semantic_type)
            role = ""
            if col.is_dimension:
                role = " [维度]"
            elif col.is_metric:
                role = " [指标]"
            unit = f" ({col.unit})" if col.unit else ""
            lines.append(f"  {col.column_letter}. {col.name} → {sem}{unit}{role}")
            if col.description:
                lines.append(f"     {col.description}")
            if col.sample_values:
                samples = ", ".join(col.sample_values[:3])
                lines.append(f"     示例: {samples}")

        # 示例数据
        if t.sample_data:
            lines.append(f"\n【示例数据】")
            for row in t.sample_data[:5]:
                lines.append(f"  | {' | '.join(str(v) for v in row)} |")

        # 分析建议
        if self.suggested_analyses:
            lines.append(f"\n【推荐分析】")
            for i, a in enumerate(self.suggested_analyses, 1):
                chart = f" [{a.chart_suggestion}图]" if a.chart_suggestion else ""
                lines.append(f"  {i}. {a.title}{chart}")
                lines.append(f"     {a.description}")
                if a.columns_involved:
                    lines.append(f"     涉及字段: {', '.join(a.columns_involved)}")

        if self.overall_summary:
            lines.append(f"\n【总结】")
            lines.append(f"  {self.overall_summary}")

        return "\n".join(lines)


class ExcelVisionAnalyzer:
    """
    Excel 截图智能分析器

    使用方式:
        analyzer = ExcelVisionAnalyzer(vision_gateway)
        result = analyzer.analyze("excel_screenshot.png")
        print(result.to_text())

        # 获取可执行的分析任务
        for task in result.suggested_analyses:
            print(task.title, task.columns_involved)
    """

    SYSTEM_PROMPT = """你是一个专业的Excel数据分析专家。用户会给你一张Excel截图，请你仔细识别并理解这张表格。

请按以下步骤分析：

1. **识别表格整体**
   - 表名/标题是什么
   - 这是什么类型的表格（销售报表/财务/库存/人事/项目/客户/采购/物流/预算/调查/排班/成绩/台账等）
   - 大约有多少行数据
   - 是否有合计行、合并单元格

2. **识别每个字段（列）**
   - 列名
   - 语义类型：日期/时间/金额/货币/数量/单价/百分比/名称/分类/产品/地区/部门/人员/编号/状态/文本/数字
   - 数据类型：text/number/date/boolean
   - 单位（元/个/%/万元等）
   - 是维度字段（用于分组/筛选）还是指标字段（用于计算）
   - 2-3个示例值

3. **推荐分析任务**
   根据表格类型和字段，推荐3-6个最有价值的分析，包括：
   - 汇总统计（总计/平均/最大/最小）
   - 趋势分析（如有日期字段）
   - 分组对比（按分类/地区/产品）
   - 排名/TOP N
   - 占比分析
   - 增长率（如有时间序列）
   - 异常检测
   - 推荐图表类型

请以JSON格式返回：
```json
{
  "title": "表名",
  "table_type": "sales",
  "table_type_confidence": 0.9,
  "description": "这是一份...",
  "data_row_count": 100,
  "header_row": 1,
  "data_start_row": 2,
  "has_total_row": false,
  "has_merged_cells": false,
  "notes": "",
  "columns": [
    {
      "name": "日期",
      "index": 0,
      "semantic_type": "date",
      "data_type": "date",
      "unit": "",
      "is_dimension": true,
      "is_metric": false,
      "description": "销售日期",
      "sample_values": ["2024-01-01", "2024-01-02"]
    }
  ],
  "sample_data": [
    ["2024-01-01", "产品A", "10000"]
  ],
  "suggested_analyses": [
    {
      "analysis_type": "trend",
      "title": "销售额趋势分析",
      "description": "按日期查看销售额变化趋势",
      "columns_involved": ["日期", "销售额"],
      "chart_suggestion": "line",
      "priority": 1,
      "formula_hint": "按日期分组SUM销售额"
    }
  ],
  "overall_summary": "一句话总结"
}
```

只返回JSON，不要其他解释。"""

    def __init__(self, vision_gateway: Optional[VisionGateway] = None):
        self.gateway = vision_gateway

    def set_gateway(self, gateway: VisionGateway):
        self.gateway = gateway

    def analyze(self, image_path: str,
                model_key: Optional[str] = None,
                extra_prompt: str = "") -> ExcelVisionResult:
        """
        分析 Excel 截图

        Args:
            image_path: 截图路径
            model_key: 指定视觉模型
            extra_prompt: 额外提示（如"重点关注金额列"）
        """
        result = ExcelVisionResult(image_path=image_path)

        if not self.gateway:
            result.error = "未配置 VisionGateway"
            return result

        if not os.path.exists(image_path):
            result.error = f"图片不存在: {image_path}"
            return result

        # 构建提示
        prompt = "请识别这张Excel截图中的表格结构、字段含义，并推荐分析任务。"
        if extra_prompt:
            prompt += f"\n\n额外要求：{extra_prompt}"

        request = VisionRequest(
            images=[ImageInput.from_file(image_path)],
            prompt=prompt,
            task_type=VisionTaskType.TABLE_EXTRACT,
            system_prompt=self.SYSTEM_PROMPT,
            require_structured=True,
            temperature=0.1,
            max_tokens=4096,
        )

        resp = self.gateway.analyze(request, model_key=model_key)

        if not resp.success:
            result.error = f"视觉分析失败: {resp.error}"
            result.model_used = resp.model_used
            return result

        result.raw_response = resp.content
        result.model_used = resp.model_used

        # 解析结果
        parsed = self._parse_response(resp.content)
        if not parsed:
            result.error = "无法解析模型返回结果"
            return result

        # 填充表格信息
        result.table = self._build_table(parsed)

        # 填充分析建议
        result.suggested_analyses = self._build_analyses(parsed)

        # 总结
        result.overall_summary = parsed.get("overall_summary", "")

        result.success = True
        return result

    def analyze_multiple(self, image_paths: List[str],
                         model_key: Optional[str] = None) -> List[ExcelVisionResult]:
        """分析多张截图（同一Excel的不同区域/Sheet）"""
        return [self.analyze(p, model_key) for p in image_paths]

    def generate_analysis_plan(self, result: ExcelVisionResult) -> Dict[str, Any]:
        """
        根据识别结果生成可执行的分析计划

        返回结构化的分析计划，可对接 ExcelOrchestrator / AnalysisEngine
        """
        if not result.success:
            return {"error": result.error}

        table = result.table
        plan: Dict[str, Any] = {
            "table_name": table.title,
            "table_type": table.table_type,
            "columns": [],
            "dimensions": [],
            "metrics": [],
            "date_columns": [],
            "analyses": [],
        }

        for col in table.columns:
            col_info = {
                "name": col.name,
                "letter": col.column_letter,
                "semantic_type": col.semantic_type,
                "data_type": col.data_type,
                "unit": col.unit,
                "is_dimension": col.is_dimension,
                "is_metric": col.is_metric,
            }
            plan["columns"].append(col_info)
            if col.is_dimension:
                plan["dimensions"].append(col.name)
            if col.is_metric:
                plan["metrics"].append(col.name)
            if col.semantic_type in ("date", "time", "datetime"):
                plan["date_columns"].append(col.name)

        for a in result.suggested_analyses:
            plan["analyses"].append({
                "type": a.analysis_type,
                "title": a.title,
                "description": a.description,
                "columns": a.columns_involved,
                "chart": a.chart_suggestion,
                "priority": a.priority,
                "formula_hint": a.formula_hint,
            })

        return plan

    def _parse_response(self, text: str) -> Optional[dict]:
        """解析模型返回的 JSON"""
        json_str = self._extract_json(text)
        if not json_str:
            return None
        try:
            return json.loads(json_str, strict=False)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_json(text: str) -> str:
        text = re.sub(r'```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```', '', text)
        start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                start = i
                break
        if start < 0:
            return ""
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
        return ""

    @staticmethod
    def _to_int(data: dict, key: str, default: int) -> int:
        """LLM 数值字段宽容解析（"约100行" → 100，解析失败用默认值）"""
        import re as _re
        raw = data.get(key, default)
        if isinstance(raw, bool) or raw is None:
            return default
        if isinstance(raw, (int, float)):
            return int(raw)
        m = _re.search(r"-?\d+", str(raw))
        return int(m.group()) if m else default

    def _build_table(self, data: dict) -> RecognizedTable:
        """从解析的数据构建 RecognizedTable"""
        try:
            confidence = float(data.get("table_type_confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        table = RecognizedTable(
            title=data.get("title", "") or "",
            table_type=data.get("table_type", "unknown") or "unknown",
            table_type_confidence=confidence,
            description=data.get("description", "") or "",
            data_row_count=self._to_int(data, "data_row_count", 0),
            header_row=self._to_int(data, "header_row", 1),
            data_start_row=self._to_int(data, "data_start_row", 2),
            data_end_row=self._to_int(data, "data_end_row", 0),
            has_total_row=bool(data.get("has_total_row", False)),
            has_merged_cells=bool(data.get("has_merged_cells", False)),
            notes=data.get("notes", "") or "",
            sample_data=data.get("sample_data", []),
        )

        for i, col_data in enumerate(data.get("columns", [])):
            col = RecognizedColumn(
                name=col_data.get("name", ""),
                index=col_data.get("index", i),
                column_letter=col_data.get("column_letter", self._index_to_letter(i)),
                semantic_type=col_data.get("semantic_type", "unknown"),
                data_type=col_data.get("data_type", "text"),
                unit=col_data.get("unit", ""),
                is_dimension=col_data.get("is_dimension", False),
                is_metric=col_data.get("is_metric", False),
                description=col_data.get("description", ""),
                sample_values=col_data.get("sample_values", []),
            )
            table.columns.append(col)

        return table

    def _build_analyses(self, data: dict) -> List[SuggestedAnalysis]:
        """构建分析建议列表"""
        analyses = []
        for a_data in data.get("suggested_analyses", []):
            analysis = SuggestedAnalysis(
                analysis_type=a_data.get("analysis_type", "summary"),
                title=a_data.get("title", ""),
                description=a_data.get("description", ""),
                columns_involved=a_data.get("columns_involved", []),
                chart_suggestion=a_data.get("chart_suggestion", ""),
                priority=int(a_data.get("priority", 3)),
                formula_hint=a_data.get("formula_hint", ""),
            )
            analyses.append(analysis)

        # 按优先级排序
        analyses.sort(key=lambda x: x.priority)
        return analyses

    @staticmethod
    def _index_to_letter(index: int) -> str:
        """0-based 列序号转 Excel 列字母 (0->A, 1->B, 26->AA)"""
        result = ""
        index += 1
        while index > 0:
            index -= 1
            result = chr(65 + index % 26) + result
            index //= 26
        return result


def analyze_excel_screenshot(image_path: str,
                             gateway: Optional[VisionGateway] = None,
                             model_key: Optional[str] = None) -> ExcelVisionResult:
    """
    便捷函数：分析 Excel 截图

    Args:
        image_path: 截图路径
        gateway: VisionGateway 实例
        model_key: 指定模型
    """
    analyzer = ExcelVisionAnalyzer(vision_gateway=gateway)
    return analyzer.analyze(image_path, model_key=model_key)
