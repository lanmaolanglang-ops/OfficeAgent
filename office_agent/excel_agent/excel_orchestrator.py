"""
Excel Orchestrator - Excel Agent 总控

流程：
用户需求/文件 → 任务分析 → 数据理解 → 公式/图表生成 → Excel Service → 质量检查 → 输出
"""
import logging
from pathlib import Path

from .models import (
    ExcelResult, DataProfile,
)
from .excel_service import ExcelService
from .data_analyzer import DataAnalyzer
from .formula_generator import FormulaGenerator
from .chart_generator import ChartGenerator
from .quality_checker import ExcelQualityChecker
from .template_analyzer import ExcelTemplateAnalyzer

logger = logging.getLogger("office_agent.excel_agent.excel_orchestrator")

# Single authoritative intent vocabulary shared by process_file and
# create_from_data (P3-21): the two entry paths used to drift apart, so the same
# user wording (e.g. "平均"/"sum"/"占比"/"chart") was honoured on one path and
# silently ignored on the other. Matching is always case-insensitive.
FORMULA_INTENT_KEYWORDS = (
    "计算", "求和", "合计", "平均", "公式", "calculate", "sum", "formula",
)
SUMMARY_INTENT_KEYWORDS = ("汇总", "summary", "总计行")
CHART_INTENT_KEYWORDS = ("图", "chart", "趋势", "对比", "占比", "可视化")


def _task_has_any(task: str, keywords) -> bool:
    """Case-insensitive substring intent match (empty task never matches)."""
    if not task:
        return False
    text = task.lower()
    return any(kw in text for kw in keywords)


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

        request-scoped：service/generators 在方法内新建，使同一
        Orchestrator 实例可被并发复用而不串状态（P2-34）。
        """
        service = ExcelService()
        formula_gen = FormulaGenerator()
        chart_gen = ChartGenerator()
        try:
            if not Path(file_path).exists():
                return ExcelResult(success=False, message=f"文件不存在: {file_path}")

            # 1. 数据理解
            profile = self.analyzer.analyze(file_path)
            formula_gen.set_profile(profile)
            chart_gen.set_profile(profile)

            # 2. 打开文件
            service.open(file_path)

            fragile = self._detect_fragile_elements(file_path, wb=service.wb)

            # 3. 根据任务执行操作
            changes = []
            if fragile:
                changes.append(f"注意：源文件包含{fragile}，已尽量保留，建议打开输出确认")

            # 解析任务意图
            wants_formulas = _task_has_any(task, FORMULA_INTENT_KEYWORDS)
            wants_explicit_summary = _task_has_any(task, SUMMARY_INTENT_KEYWORDS)
            wants_chart = _task_has_any(task, CHART_INTENT_KEYWORDS)

            # 每个工作表独立执行，避免多 Sheet 文件只有第一页被处理。
            for sheet in profile.sheets:
                sheet_name = sheet.name
                if wants_formulas:
                    formulas = formula_gen.generate_from_text(task, sheet_name)
                    service.add_formulas(formulas, sheet_name)
                    if formulas:
                        changes.append(f"{sheet_name}: 添加 {len(formulas)} 个公式")

                if add_summary and (not wants_formulas or wants_explicit_summary):
                    num_cols = [c.index for c in sheet.columns if c.data_type == "number"]
                    if num_cols:
                        service.add_summary_row(sheet_name, "合计", num_cols)
                        changes.append(f"{sheet_name}: 添加汇总行")

                if add_charts:
                    charts = (
                        chart_gen.generate_from_text(
                            task, sheet_name, chart_type=chart_type
                        ) if wants_chart else chart_gen.auto_charts(sheet_name)
                    )
                    if charts:
                        service.add_charts(charts, sheet_name)
                        changes.append(f"{sheet_name}: 添加 {len(charts)} 个图表")

                if add_format:
                    service.apply_header_style(sheet_name)
                    service.auto_width(sheet_name)
                    service.freeze_header(sheet_name)
                    if sheet.row_count > 5:
                        service.add_filter(sheet_name)
                    changes.append(f"{sheet_name}: 应用格式化")

                num_cols = [c for c in sheet.columns if c.data_type == "number"]
                if num_cols and sheet.row_count > 0:
                    from openpyxl.utils import get_column_letter
                    for col in num_cols:
                        col_letter = get_column_letter(col.index + 1)
                        range_str = f"{col_letter}2:{col_letter}{sheet.row_count + 1}"
                        service.add_conditional_format(sheet_name, range_str, "data_bar")

            # 4. 保存
            if not output_path:
                stem = Path(file_path).stem
                output_path = f"{stem}_processed.xlsx"

            result = service.save(output_path)
            result.changes.extend(changes)

            # 5. 质量检查
            quality = self.quality_checker.check_with_score(output_path)
            result.quality_score = quality["score"]
            result.quality_issues = quality["issues"]
            result.message = f"处理完成: {Path(output_path).name}, 质量分{quality['score']:.0f}"
            if quality["error_count"] > 0:
                result.message += f", {quality['error_count']}个错误"
            if fragile:
                result.message += f"（源文件含{fragile}，建议打开确认完整性）"

            # 数据预览
            preview_sheet = profile.sheets[0].name if profile.sheets else None
            result.data_preview = service.get_preview(preview_sheet, rows=5)

            return result

        except Exception as e:
            return ExcelResult(
                success=False,
                message=f"处理失败: {str(e)}",
            )
        finally:
            service.close()

    @staticmethod
    def _detect_fragile_elements(file_path: str, wb=None) -> str:
        """检测 openpyxl 往返可能不完整的元素类型（best-effort）。

        传入已加载的 ``wb`` 时直接复用，避免为检测再整本解析一次文件；
        未传入时按旧行为自行打开（供外部/测试调用）。
        """
        kinds = []
        try:
            owns_wb = wb is None
            if owns_wb:
                from openpyxl import load_workbook
                wb = load_workbook(file_path, read_only=False, data_only=False)
            try:
                for ws in wb.worksheets:
                    if getattr(ws, "_charts", None):
                        kinds.append("图表")
                        break
                for ws in wb.worksheets:
                    if getattr(ws, "_images", None):
                        kinds.append("图片")
                        break
                for ws in wb.worksheets:
                    if getattr(ws, "_pivots", None):
                        kinds.append("数据透视表")
                        break
            finally:
                if owns_wb:
                    wb.close()
        except Exception as exc:
            # best-effort 检测失败：降级为无提示，但原因必须可观测
            logger.warning(
                "脆弱元素检测失败，降级为无提示继续 %s: %s",
                file_path, exc, exc_info=True,
            )
            return ""
        seen = []
        for k in ("数据透视表", "图表", "图片"):
            if k in kinds:
                seen.append(k)
        return "/".join(seen)

    def create_from_data(self, data: list,
                         task: str = "",
                         sheet_name: str = "Sheet1",
                         output_path: str = "output.xlsx",
                         headers: list | None = None,
                         has_header: bool = True) -> ExcelResult:
        """
        从数据创建 Excel

        Args:
            data: 二维列表（不含表头）或 DataFrame
            task: 任务描述
            sheet_name: 工作表名
            output_path: 输出路径
            headers: 表头列表；提供时 data 始终按纯数据行解释
            has_header: 未提供 headers 时，data 首行是否为表头（兼容旧调用）
        """
        service = ExcelService()
        formula_gen = FormulaGenerator()
        chart_gen = ChartGenerator()
        try:
            service.create(output_path, sheet_name)

            rows = list(data or [])
            if headers is not None:
                normalized_headers = list(headers)
                body_rows = rows
            elif has_header and rows:
                normalized_headers = list(rows[0])
                body_rows = rows[1:]
            elif rows:
                width = max((len(row) for row in rows if isinstance(row, (list, tuple))), default=0)
                normalized_headers = [f"列{i + 1}" for i in range(width)]
                body_rows = rows
            else:
                normalized_headers = []
                body_rows = []

            full_data = ([normalized_headers] if normalized_headers else []) + body_rows

            service.write_data(sheet_name, full_data, has_header=True)

            # 分析数据
            import pandas as pd
            df = pd.DataFrame(body_rows, columns=normalized_headers or None)
            profile = self.analyzer.analyze_dataframe(df, sheet_name)
            formula_gen.set_profile(profile)
            chart_gen.set_profile(profile)

            changes = ["创建新文件"]

            # 公式
            if normalized_headers and _task_has_any(task, FORMULA_INTENT_KEYWORDS):
                formulas = formula_gen.generate_from_text(task, sheet_name)
                service.add_formulas(formulas, sheet_name)
                changes.append(f"添加 {len(formulas)} 个公式")
            elif normalized_headers:
                # 默认汇总
                num_cols = [c.index for c in profile.sheets[0].columns if c.data_type == "number"]
                if num_cols:
                    service.add_summary_row(sheet_name, "合计", num_cols)
                    changes.append("添加汇总行")

            # 图表
            if normalized_headers and _task_has_any(task, CHART_INTENT_KEYWORDS):
                charts = chart_gen.generate_from_text(task, sheet_name)
            elif normalized_headers:
                charts = chart_gen.auto_charts(sheet_name)
            else:
                charts = []
            if charts:
                service.add_charts(charts, sheet_name)
                changes.append(f"添加 {len(charts)} 个图表")

            # 格式化
            service.auto_width(sheet_name)
            service.freeze_header(sheet_name)
            service.add_filter(sheet_name)
            changes.append("应用格式化")

            # 保存
            result = service.save(output_path)
            result.changes.extend(changes)

            # 质量检查
            quality = self.quality_checker.check_with_score(output_path)
            result.quality_score = quality["score"]
            result.quality_issues = quality["issues"]
            result.message = f"创建完成: {Path(output_path).name}, 质量分{quality['score']:.0f}"
            result.data_preview = service.get_preview(sheet_name, rows=5)

            return result

        except Exception as e:
            return ExcelResult(
                success=False,
                message=f"创建失败: {str(e)}",
            )
        finally:
            service.close()

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
        """应用模板格式到数据文件（request-scoped service，P2-34）"""
        service = ExcelService()
        try:
            tpl_config = self.template_analyzer.analyze(template_path)
            service.open(data_path)
            # P3-22: apply a template only to data sheets that actually exist.
            # Previously a template sheet whose name matched nothing was
            # silently skipped (apply_format no-ops on a missing sheet) while the
            # result still claimed "模板应用完成". Track matched/unmatched and
            # report truthfully; fail loudly if no template sheet matched at all.
            data_sheet_names = set(service.workbook.sheetnames)
            unmatched = []
            matched = 0
            for sheet_tpl in tpl_config.sheets:
                if sheet_tpl.name not in data_sheet_names:
                    unmatched.append(sheet_tpl.name)
                    continue
                matched += 1
                self.template_analyzer.apply_to_service(
                    service, tpl_config, sheet_tpl.name
                )
            if tpl_config.sheets and matched == 0:
                return ExcelResult(
                    success=False,
                    message=(
                        "模板应用失败：模板工作表名与数据文件均不匹配 "
                        f"(模板: {[s.name for s in tpl_config.sheets]}, "
                        f"数据: {sorted(data_sheet_names)})"
                    ),
                )
            if not output_path:
                stem = Path(data_path).stem
                output_path = f"{stem}_templated.xlsx"
            result = service.save(output_path)
            note = f"模板应用完成: {Path(output_path).name}"
            if unmatched:
                note += f"；{len(unmatched)} 个模板表无对应数据表已跳过: {unmatched}"
            result.message = note
            return result
        except Exception as e:
            return ExcelResult(
                success=False,
                message=f"模板应用失败: {str(e)}",
            )
        finally:
            service.close()
