"""
Excel Quality Scorer - Excel表格质量评分引擎

评分维度：
1. 公式正确率 (formula_accuracy): 公式是否正确、计算结果是否准确
2. 数据分析准确率 (analysis_accuracy): 数据分析结果是否正确
3. 图表合理性 (chart_appropriateness): 图表类型选择、数据范围、标签
"""
import os
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


@dataclass
class ExcelScoreResult:
    """Excel评分结果"""
    file_path: str = ""
    total_score: float = 0.0

    # 各维度分数 (0-100)
    formula_accuracy: float = 0.0
    analysis_accuracy: float = 0.0
    chart_appropriateness: float = 0.0

    # 详细信息
    formula_details: Dict[str, Any] = field(default_factory=dict)
    analysis_details: Dict[str, Any] = field(default_factory=dict)
    chart_details: Dict[str, Any] = field(default_factory=dict)

    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "total_score": round(self.total_score, 1),
            "scores": {
                "formula_accuracy": round(self.formula_accuracy, 1),
                "analysis_accuracy": round(self.analysis_accuracy, 1),
                "chart_appropriateness": round(self.chart_appropriateness, 1),
            },
            "details": {
                "formula": self.formula_details,
                "analysis": self.analysis_details,
                "chart": self.chart_details,
            },
            "issues": self.issues,
            "suggestions": self.suggestions,
        }


class ExcelQualityScorer:
    """Excel质量评分器"""

    # 总分权重（三者之和必须为 1.0）：公式准确性 > 分析准确性 > 图表恰当性
    WEIGHT_FORMULA_ACCURACY = 0.4
    WEIGHT_ANALYSIS_ACCURACY = 0.35
    WEIGHT_CHART_APPROPRIATENESS = 0.25

    # 常见函数列表
    COMMON_FUNCTIONS = {
        "SUM", "AVERAGE", "COUNT", "COUNTA", "MAX", "MIN",
        "IF", "VLOOKUP", "HLOOKUP", "INDEX", "MATCH",
        "SUMIF", "COUNTIF", "AVERAGEIF", "SUMIFS", "COUNTIFS",
        "RANK", "RANK.EQ", "ROUND", "ABS", "POWER",
        "TEXT", "LEFT", "RIGHT", "MID", "LEN",
        "TODAY", "NOW", "YEAR", "MONTH", "DAY",
    }

    def score(self, file_path: str,
             expected_formulas: List[str] | None = None,
             expected_charts: List[str] | None = None,
             expected_sheets: List[str] | None = None) -> ExcelScoreResult:
        """
        评分Excel

        Args:
            file_path: xlsx文件路径
            expected_formulas: 期望包含的公式类型
            expected_charts: 期望包含的图表类型
            expected_sheets: 期望包含的Sheet名
        """
        result = ExcelScoreResult(file_path=file_path)

        if not os.path.exists(file_path):
            result.issues.append(f"文件不存在: {file_path}")
            return result

        try:
            wb = load_workbook(file_path, data_only=False)
            wb_data = load_workbook(file_path, data_only=True)
        except Exception as e:
            result.issues.append(f"无法打开文件: {e}")
            return result

        # 分析工作簿
        analysis = self._analyze_workbook(wb, wb_data)

        # 各维度评分
        result.formula_accuracy = self._score_formulas(analysis, expected_formulas)
        result.analysis_accuracy = self._score_analysis(analysis, expected_sheets)
        result.chart_appropriateness = self._score_charts(analysis, expected_charts)

        result.formula_details = analysis.get("formula", {})
        result.analysis_details = analysis.get("analysis", {})
        result.chart_details = analysis.get("chart", {})

        # 总分
        result.total_score = (
            result.formula_accuracy * self.WEIGHT_FORMULA_ACCURACY +
            result.analysis_accuracy * self.WEIGHT_ANALYSIS_ACCURACY +
            result.chart_appropriateness * self.WEIGHT_CHART_APPROPRIATENESS
        )

        result.issues = self._collect_issues(analysis, expected_formulas,
                                             expected_charts, expected_sheets)
        result.suggestions = self._collect_suggestions(analysis)

        return result

    def _analyze_workbook(self, wb, wb_data) -> Dict[str, Any]:
        """分析工作簿"""
        sheets_info = []
        total_formulas = 0
        total_cells_with_data = 0
        formula_types_found = set()
        formula_errors = 0
        chart_count = 0
        chart_types = []
        total_sheets = len(wb.sheetnames)

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            ws_data = wb_data[sheet_name]

            sheet_info = {
                "name": sheet_name,
                "rows": ws.max_row,
                "cols": ws.max_column,
                "formulas": [],
                "formula_count": 0,
                "data_cells": 0,
                "charts": [],
                "has_header": False,
                "header_row": [],
            }

            # 检测表头
            if ws.max_row > 1:
                headers = []
                for col in range(1, min(ws.max_column + 1, 20)):
                    val = ws.cell(row=1, column=col).value
                    if val and isinstance(val, str):
                        headers.append(str(val))
                if len(headers) >= 2:
                    sheet_info["has_header"] = True
                    sheet_info["header_row"] = headers

            # 扫描单元格
            for row in range(1, ws.max_row + 1):
                for col in range(1, ws.max_column + 1):
                    cell = ws.cell(row=row, column=col)
                    cell_data = ws_data.cell(row=row, column=col)

                    if cell.value is not None:
                        total_cells_with_data += 1
                        sheet_info["data_cells"] += 1

                    # 检查公式
                    if isinstance(cell.value, str) and cell.value.startswith("="):
                        formula = cell.value
                        sheet_info["formula_count"] += 1
                        total_formulas += 1

                        # 提取函数名
                        func_name = self._extract_function_name(formula)
                        if func_name:
                            formula_types_found.add(func_name)

                        # 检查计算结果和公式文本。openpyxl 不会计算公式，
                        # 自产文件通常没有缓存值，因此必须做静态错误检查。
                        data_val = cell_data.value
                        has_error = self._has_static_formula_error(formula)
                        if isinstance(data_val, str) and data_val.startswith("#"):
                            has_error = True
                        if has_error:
                            formula_errors += 1

                        sheet_info["formulas"].append({
                            "cell": f"{get_column_letter(col)}{row}",
                            "formula": formula[:50],
                            "function": func_name,
                            "result": str(data_val)[:30] if data_val is not None else None,
                            "has_error": has_error,
                        })

            # 检查图表
            if hasattr(ws, '_charts'):
                for chart in ws._charts:
                    chart_count += 1
                    chart_type = type(chart).__name__
                    chart_types.append(chart_type)
                    sheet_info["charts"].append({
                        "type": chart_type,
                        "title": str(chart.title) if chart.title else "",
                    })

            sheets_info.append(sheet_info)

        # 计算统计
        formula_error_rate = formula_errors / max(total_formulas, 1)

        # 分析维度
        analysis_sheets = [s for s in sheets_info
                          if any(kw in s["name"] for kw in
                                ["汇总", "分析", "统计", "结果", "趋势", "summary", "analysis"])]

        return {
            "total_sheets": total_sheets,
            "sheet_names": wb.sheetnames,
            "total_formulas": total_formulas,
            "total_data_cells": total_cells_with_data,
            "formula_types": sorted(list(formula_types_found)),
            "formula_count": len(formula_types_found),
            "formula_errors": formula_errors,
            "formula_error_rate": round(formula_error_rate, 3),
            "chart_count": chart_count,
            "chart_types": chart_types,
            "sheets": sheets_info,
            "analysis_sheets": analysis_sheets,
            "formula": {
                "total_formulas": total_formulas,
                "formula_types": sorted(list(formula_types_found)),
                "formula_count": len(formula_types_found),
                "error_count": formula_errors,
                "error_rate": round(formula_error_rate, 3),
            },
            "analysis": {
                "total_sheets": total_sheets,
                "sheet_names": wb.sheetnames,
                "analysis_sheet_count": len(analysis_sheets),
                "has_headers": sum(1 for s in sheets_info if s["has_header"]),
            },
            "chart": {
                "chart_count": chart_count,
                "chart_types": chart_types,
            },
        }

    @staticmethod
    def _has_static_formula_error(formula: str) -> bool:
        upper = formula.upper()
        if any(token in upper for token in (
            "#REF!", "#DIV/0!", "#VALUE!", "#N/A", "#NAME?", "#NUM!", "#NULL!"
        )):
            return True
        # 明确的常量除零可在没有 Excel 计算引擎时可靠判定。
        if re.search(r"/\s*(?:0+(?:\.0*)?)\s*(?:[+\-*/^),]|$)", formula):
            return True
        return False

    def _extract_function_name(self, formula: str) -> Optional[str]:
        """从公式中提取函数名"""
        # 去掉=号
        f = formula.lstrip("=")
        # 找第一个(
        if "(" in f:
            func = f[:f.index("(")].upper().strip()
            # 去掉可能的前缀（如工作表名!）
            if "!" in func:
                func = func.split("!")[-1]
            if func and func.isalpha():
                return func
        return None

    def _score_formulas(self, analysis: Dict,
                       expected: List[str] | None = None) -> float:
        """公式正确率评分"""
        score = 40.0  # 基础分

        total = analysis["total_formulas"]
        if total == 0:
            return 20  # 没有公式，低分

        # 有公式
        score += 15

        # 公式类型丰富
        fc = analysis["formula_count"]
        if fc >= 4:
            score += 15
        elif fc >= 2:
            score += 10
        elif fc >= 1:
            score += 5

        # 无错误
        error_rate = analysis["formula_error_rate"]
        if error_rate == 0:
            score += 20
        elif error_rate < 0.1:
            score += 12
        elif error_rate < 0.3:
            score += 5

        # 检查期望公式
        if expected:
            found = set(analysis["formula_types"])
            matched = sum(1 for e in expected if any(
                e.upper() in f or f in e.upper()
                for f in found
            ))
            match_rate = matched / len(expected)
            score += match_rate * 10

        return min(100, score)

    def _score_analysis(self, analysis: Dict,
                       expected_sheets: List[str] | None = None) -> float:
        """数据分析准确率评分"""
        score = 40.0

        # 有多个Sheet（可能有分析）
        ts = analysis["total_sheets"]
        if ts >= 4:
            score += 15
        elif ts >= 2:
            score += 10
        elif ts >= 1:
            score += 5

        # 有分析类Sheet
        asc = analysis.get("analysis", {}).get("analysis_sheet_count", 0)
        if asc >= 3:
            score += 20
        elif asc >= 1:
            score += 12

        # 有表头
        if analysis.get("analysis", {}).get("has_headers", 0) >= 1:
            score += 10

        # 有公式（说明有计算）
        if analysis["total_formulas"] > 0:
            score += 10

        # 检查期望Sheet
        if expected_sheets:
            found = set(analysis["sheet_names"])
            matched = sum(1 for e in expected_sheets if e in found)
            match_rate = matched / len(expected_sheets)
            score += match_rate * 15

        return min(100, score)

    def _score_charts(self, analysis: Dict,
                     expected_charts: List[str] | None = None) -> float:
        """图表合理性评分"""
        score = 30.0

        cc = analysis["chart_count"]
        if cc >= 3:
            score += 25
        elif cc >= 2:
            score += 20
        elif cc >= 1:
            score += 15

        # 图表类型多样
        ct = len(set(analysis["chart_types"]))
        if ct >= 2:
            score += 15
        elif ct >= 1:
            score += 8

        # 有数据才能做图表
        if analysis["total_data_cells"] > 10:
            score += 10

        # 检查期望图表类型
        if expected_charts:
            chart_types_lower = [t.lower() for t in analysis["chart_types"]]
            matched = 0
            for exp in expected_charts:
                exp_lower = exp.lower()
                if any(exp_lower in ct or ct in exp_lower for ct in chart_types_lower):
                    matched += 1
                # 中文匹配
                elif any("bar" in chart_type for chart_type in chart_types_lower) and "柱" in exp:
                    matched += 1
                elif any("line" in chart_type for chart_type in chart_types_lower) and "线" in exp:
                    matched += 1
                elif any("pie" in chart_type for chart_type in chart_types_lower) and "饼" in exp:
                    matched += 1
            match_rate = matched / len(expected_charts) if expected_charts else 0
            score += match_rate * 20

        # 如果没有图表但也没要求，给中等分
        if cc == 0 and not expected_charts:
            score = 50

        return min(100, score)

    def _collect_issues(self, analysis, expected_formulas,
                       expected_charts, expected_sheets) -> List[str]:
        issues = []
        if analysis["total_formulas"] == 0:
            issues.append("未检测到公式")
        if analysis["formula_errors"] > 0:
            issues.append(f"存在{analysis['formula_errors']}个公式错误")
        if analysis["chart_count"] == 0 and expected_charts:
            issues.append("未检测到图表")
        if expected_formulas:
            missing = [e for e in expected_formulas
                      if e.upper() not in " ".join(analysis["formula_types"])]
            if missing:
                issues.append(f"缺少公式类型: {', '.join(missing)}")
        if expected_sheets:
            missing_sheets = [s for s in expected_sheets
                            if s not in analysis["sheet_names"]]
            if missing_sheets:
                issues.append(f"缺少Sheet: {', '.join(missing_sheets)}")
        return issues

    def _collect_suggestions(self, analysis) -> List[str]:
        suggestions = []
        if analysis["total_formulas"] == 0:
            suggestions.append("建议添加计算公式（如SUM、AVERAGE等）")
        if analysis["formula_count"] < 3 and analysis["total_data_cells"] > 20:
            suggestions.append("数据量较大，建议添加更多分析公式")
        if analysis["chart_count"] == 0 and analysis["total_data_cells"] > 15:
            suggestions.append("建议添加图表以可视化数据")
        if analysis["total_sheets"] == 1 and analysis["total_data_cells"] > 30:
            suggestions.append("建议将分析结果放在单独的Sheet中")
        return suggestions
