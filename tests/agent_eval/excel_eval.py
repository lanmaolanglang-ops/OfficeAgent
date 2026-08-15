"""
Excel Agent 评估器
"""
import time
from pathlib import Path
from .evaluator import BaseEvaluator, EvalResult, EvalType, EvalStatus, EvalMetric


class ExcelEvaluator(BaseEvaluator):
    """Excel Agent 输出质量评估"""

    def evaluate_analysis(self, output_path: str | Path) -> EvalResult:
        """评估Excel分析结果质量"""
        start = time.time()
        metrics = []
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(output_path), data_only=False)

            # 1. 文件完整性
            metrics.append(EvalMetric("文件完整性", 100, weight=2.0))

            # 2. 工作表数量
            sheet_count = len(wb.sheetnames)
            sheet_score = min(100, sheet_count * 25) if sheet_count > 0 else 0
            metrics.append(EvalMetric("工作表数量", sheet_score, weight=1.0,
                                      details=f"{sheet_count}个表"))

            # 3. 数据完整性
            ws = wb.active
            row_count = ws.max_row
            col_count = ws.max_column
            data_score = min(100, row_count * 5) if row_count > 1 else 0
            metrics.append(EvalMetric("数据完整性", data_score, weight=1.5,
                                      details=f"{row_count}行x{col_count}列"))

            # 4. 公式正确性
            formula_count = 0
            formula_errors = 0
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value and isinstance(cell.value, str) and cell.value.startswith("="):
                        formula_count += 1
            formula_score = 100 if formula_count > 0 else 70
            metrics.append(EvalMetric("公式覆盖", formula_score, weight=1.5,
                                      details=f"{formula_count}个公式"))

            # 5. 表头完整性
            headers = [ws.cell(1, c).value for c in range(1, col_count + 1)]
            header_score = 100 if all(headers) else 50
            metrics.append(EvalMetric("表头完整性", header_score, weight=1.0,
                                      details=f"表头{headers}"))

            status = EvalStatus.PASS if all(m.score >= 50 for m in metrics) else EvalStatus.PARTIAL

        except Exception as e:
            metrics.append(EvalMetric("错误", 0, details=str(e)))
            status = EvalStatus.ERROR

        return EvalResult(
            eval_type=EvalType.EXCEL,
            test_name="excel_analysis_eval",
            status=status,
            metrics=metrics,
            duration=time.time() - start,
        )

    def evaluate_formula_accuracy(self, output_path: str | Path,
                                  expected_results: dict = None) -> EvalResult:
        """评估公式计算准确性"""
        start = time.time()
        metrics = []
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(output_path), data_only=True)
            ws = wb.active

            # 检查是否有计算结果
            has_data = False
            numeric_count = 0
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, (int, float)):
                        numeric_count += 1
                        has_data = True

            metrics.append(EvalMetric("计算结果", 100 if has_data else 0, weight=2.0,
                                      details=f"{numeric_count}个数值"))

            # 如果有期望结果，对比
            if expected_results:
                correct = 0
                for cell_ref, expected in expected_results.items():
                    actual = ws[cell_ref].value
                    if actual == expected or (
                        isinstance(actual, (int, float)) and isinstance(expected, (int, float))
                        and abs(actual - expected) < 0.01
                    ):
                        correct += 1
                accuracy = correct / len(expected_results) * 100
                metrics.append(EvalMetric("结果准确性", accuracy, weight=2.0,
                                          details=f"{correct}/{len(expected_results)}正确"))

            status = EvalStatus.PASS if all(m.score >= 70 for m in metrics) else EvalStatus.PARTIAL

        except Exception as e:
            metrics.append(EvalMetric("错误", 0, details=str(e)))
            status = EvalStatus.ERROR

        return EvalResult(
            eval_type=EvalType.EXCEL,
            test_name="excel_formula_eval",
            status=status,
            metrics=metrics,
            duration=time.time() - start,
        )

    def evaluate_charts(self, output_path: str | Path) -> EvalResult:
        """评估图表生成"""
        start = time.time()
        metrics = []
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(output_path))
            ws = wb.active

            chart_count = len(ws._charts) if hasattr(ws, "_charts") else 0
            chart_score = min(100, chart_count * 30) if chart_count > 0 else 50
            metrics.append(EvalMetric("图表数量", chart_score, weight=1.5,
                                      details=f"{chart_count}个图表"))

            status = EvalStatus.PASS if chart_count > 0 else EvalStatus.PARTIAL

        except Exception as e:
            metrics.append(EvalMetric("错误", 0, details=str(e)))
            status = EvalStatus.ERROR

        return EvalResult(
            eval_type=EvalType.EXCEL,
            test_name="excel_chart_eval",
            status=status,
            metrics=metrics,
            duration=time.time() - start,
        )
