"""
Excel Orchestrator - Excel Agent 总控

流程：
用户需求/文件 → 任务分析 → 数据理解 → 公式/图表生成 → Excel Service → 质量检查 → 输出
"""
from pathlib import Path
from typing import Optional, List, Dict, Any
import re

from .models import (
    ExcelTask, TaskType, ExcelResult, DataProfile,
    FormulaSpec, ChartSpec, FormatSpec,
)
from .excel_service import ExcelService
from .data_analyzer import DataAnalyzer
from .formula_generator import FormulaGenerator
from .chart_generator import ChartGenerator
from .quality_checker import ExcelQualityChecker
from .template_analyzer import ExcelTemplateAnalyzer


class ExcelOrchestrator:
    """
    Excel Agent 总控

    用法:
        agent = ExcelOrchestrator()

        # 1. 从已有文件处理
        result = agent.process_file(
            file_path="data.xlsx",
            task="计算各列合计并生成柱状图",
            output_path="output.xlsx"
        )

        # 2. 从数据创建
        result = agent.create_from_data(
            data=[["名称", "销售额"], ["A", 100], ["B", 200]],
            task="添加合计行和图表",
            output_path="output.xlsx"
        )

        # 3. 分析文件
        profile = agent.analyze("data.xlsx")
        print(profile.summary)
    """

    def __init__(self):
        self.service = ExcelService()
        self.analyzer = DataAnalyzer()
        self.formula_gen = FormulaGenerator()
        self.chart_gen = ChartGenerator()
        self.quality_checker = ExcelQualityChecker()
        self.template_analyzer = ExcelTemplateAnalyzer()

    def analyze(self, file_path: str) -> DataProfile:
        """分析 Excel 文件，返回数据画像"""
        return self.analyzer.analyze(file_path)

    def process_file(self, file_path: str, task: str = "",
                     output_path: str = "",
                     add_summary: bool = True,
                     add_charts: bool = True,
                     add_format: bool = True,
                     chart_type: str = "") -> ExcelResult:
        """
        处理已有 Excel 文件

        Args:
            file_path: 输入文件
            task: 用户自然语言任务描述
            output_path: 输出路径
            add_summary: 是否添加汇总行
            add_charts: 是否添加图表
            add_format: 是否格式化
        """
        try:
            if not Path(file_path).exists():
                return ExcelResult(success=False, message=f"文件不存在: {file_path}")

            # 1. 数据理解
            profile = self.analyzer.analyze(file_path)
            self.formula_gen.set_profile(profile)
            self.chart_gen.set_profile(profile)

            # 2. 打开文件
            self.service.open(file_path)

            sheet_name = profile.sheets[0].name if profile.sheets else None

            # 3. 根据任务执行操作
            changes = []

            # 解析任务意图
            task_lower = task.lower() if task else ""

            # 公式计算
            if task and any(kw in task_lower for kw in
                           ["计算", "求和", "合计", "平均", "公式", "calculate", "sum", "formula"]):
                formulas = self.formula_gen.generate_from_text(task, sheet_name)
                self.service.add_formulas(formulas, sheet_name)
                changes.append(f"添加 {len(formulas)} 个公式")

            # 汇总行
            elif add_summary:
                sheet = profile.get_sheet(sheet_name)
                if sheet:
                    num_cols = [c.index for c in sheet.columns if c.data_type == "number"]
                    if num_cols:
                        self.service.add_summary_row(sheet_name, "合计", num_cols)
                        changes.append("添加汇总行")

            # 图表
            if add_charts:
                if task and any(kw in task_lower for kw in
                               ["图", "chart", "趋势", "对比", "占比", "可视化"]):
                    charts = self.chart_gen.generate_from_text(task, sheet_name, chart_type=chart_type)
                else:
                    charts = self.chart_gen.auto_charts(sheet_name)

                if charts:
                    self.service.add_charts(charts, sheet_name)
                    changes.append(f"添加 {len(charts)} 个图表")

            # 格式化
            if add_format:
                self.service.apply_header_style(sheet_name)
                self.service.auto_width(sheet_name)
                self.service.freeze_header(sheet_name)
                if profile.sheets and profile.sheets[0].row_count > 5:
                    self.service.add_filter(sheet_name)
                changes.append("应用格式化")

            # 条件格式（数值列）
            sheet = profile.get_sheet(sheet_name)
            if sheet:
                num_cols = [c for c in sheet.columns if c.data_type == "number"]
                if num_cols:
                    from openpyxl.utils import get_column_letter
                    for col in num_cols[:3]:
                        col_letter = get_column_letter(col.index + 1)
                        range_str = f"{col_letter}2:{col_letter}{sheet.row_count + 1}"
                        self.service.add_conditional_format(sheet_name, range_str, "data_bar")

            # 4. 保存
            if not output_path:
                stem = Path(file_path).stem
                output_path = f"{stem}_processed.xlsx"

            result = self.service.save(output_path)
            result.changes.extend(changes)

            # 5. 质量检查
            quality = self.quality_checker.check_with_score(output_path)
            result.quality_score = quality["score"]
            result.quality_issues = quality["issues"]
            result.message = f"处理完成: {Path(output_path).name}, 质量分{quality['score']:.0f}"
            if quality["error_count"] > 0:
                result.message += f", {quality['error_count']}个错误"

            # 数据预览
            result.data_preview = self.service.get_preview(sheet_name, rows=5)

            return result

        except Exception as e:
            return ExcelResult(
                success=False,
                message=f"处理失败: {str(e)}",
            )

    def create_from_data(self, data: list,
                         task: str = "",
                         sheet_name: str = "Sheet1",
                         output_path: str = "output.xlsx",
                         headers: list = None) -> ExcelResult:
        """
        从数据创建 Excel

        Args:
            data: 二维列表（不含表头）或 DataFrame
            task: 任务描述
            sheet_name: 工作表名
            output_path: 输出路径
            headers: 表头列表
        """
        try:
            self.service.create(output_path, sheet_name)

            # 构建完整数据
            if headers:
                full_data = [headers] + list(data)
            else:
                full_data = list(data)

            self.service.write_data(sheet_name, full_data, has_header=True)

            # 分析数据
            import pandas as pd
            if headers:
                df = pd.DataFrame(data, columns=headers)
            else:
                df = pd.DataFrame(data[1:], columns=data[0])
            profile = self.analyzer.analyze_dataframe(df, sheet_name)
            self.formula_gen.set_profile(profile)
            self.chart_gen.set_profile(profile)

            changes = ["创建新文件"]

            # 公式
            if task and any(kw in task for kw in ["计算", "求和", "合计", "公式"]):
                formulas = self.formula_gen.generate_from_text(task, sheet_name)
                self.service.add_formulas(formulas, sheet_name)
                changes.append(f"添加 {len(formulas)} 个公式")
            else:
                # 默认汇总
                num_cols = [c.index for c in profile.sheets[0].columns if c.data_type == "number"]
                if num_cols:
                    self.service.add_summary_row(sheet_name, "合计", num_cols)
                    changes.append("添加汇总行")

            # 图表
            if task and any(kw in task for kw in ["图", "趋势", "对比", "可视化"]):
                charts = self.chart_gen.generate_from_text(task, sheet_name)
            else:
                charts = self.chart_gen.auto_charts(sheet_name)
            if charts:
                self.service.add_charts(charts, sheet_name)
                changes.append(f"添加 {len(charts)} 个图表")

            # 格式化
            self.service.auto_width(sheet_name)
            self.service.freeze_header(sheet_name)
            self.service.add_filter(sheet_name)
            changes.append("应用格式化")

            # 保存
            result = self.service.save(output_path)
            result.changes.extend(changes)

            # 质量检查
            quality = self.quality_checker.check_with_score(output_path)
            result.quality_score = quality["score"]
            result.quality_issues = quality["issues"]
            result.message = f"创建完成: {Path(output_path).name}, 质量分{quality['score']:.0f}"
            result.data_preview = self.service.get_preview(sheet_name, rows=5)

            return result

        except Exception as e:
            return ExcelResult(
                success=False,
                message=f"创建失败: {str(e)}",
            )

    def create_from_dataframe(self, df, task: str = "",
                              sheet_name: str = "Sheet1",
                              output_path: str = "output.xlsx") -> ExcelResult:
        """从 pandas DataFrame 创建"""
        data = [list(df.columns)] + df.values.tolist()
        return self.create_from_data(
            data=data[1:],  # 不含表头
            task=task,
            sheet_name=sheet_name,
            output_path=output_path,
            headers=list(df.columns),
        )

    def apply_template(self, data_path: str, template_path: str,
                       output_path: str = "") -> ExcelResult:
        """应用模板格式到数据文件"""
        try:
            # 分析模板
            tpl_config = self.template_analyzer.analyze(template_path)

            # 打开数据文件
            self.service.open(data_path)

            # 应用模板格式
            for sheet_tpl in tpl_config.sheets:
                self.template_analyzer.apply_to_service(
                    self.service, tpl_config, sheet_tpl.name
                )

            if not output_path:
                stem = Path(data_path).stem
                output_path = f"{stem}_templated.xlsx"

            result = self.service.save(output_path)
            result.message = f"模板应用完成: {Path(output_path).name}"
            return result

        except Exception as e:
            return ExcelResult(
                success=False,
                message=f"模板应用失败: {str(e)}",
            )
