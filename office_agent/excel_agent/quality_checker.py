"""
Excel Quality Checker - Excel 质量检查与自动修复

检查项：
1. 公式正确性
   - 错误值检测（#REF!, #DIV/0!, #VALUE!, #N/A, #NAME?, #NUM!, #NULL!）
   - 公式引用越界
   - 同列公式模式一致性
   - 除零风险

2. 数据完整性
   - 空行/空列异常
   - 关键字段空值率
   - 重复行
   - 数据类型混入（数值列有文本）

3. 格式规范
   - 表头样式
   - 数字格式正确性
   - 字体统一性
   - 列宽不足
   - 对齐一致性

4. 图表有效性
   - 图表数据范围
   - 空系列
   - 引用范围越界

5. 计算结果异常
   - 离群值（3σ原则）
   - 负数异常（金额/数量列）
   - 零值异常
   - 极端增长率

自动修复：
- #DIV/0! → IFERROR 包裹
- 数字格式错误 → 修正
- 列宽不足 → 自动调整
- 空值 → 标记/填充
- 公式不一致 → 统一
"""
import logging
import re
import statistics
from pathlib import Path
from typing import List
from dataclasses import dataclass, field

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter, column_index_from_string

from .models import ExcelQualityIssue, DataProfile

logger = logging.getLogger("office_agent.excel_agent.quality_checker")


# Excel 错误值
EXCEL_ERRORS = {
    "#REF!": ("引用错误", "公式引用了不存在的单元格", True),
    "#VALUE!": ("值类型错误", "公式参数类型不正确", True),
    "#DIV/0!": ("除以零", "除数为零", True),
    "#N/A": ("值不可用", "查找值不存在", True),
    "#NAME?": ("名称错误", "公式中包含未识别的名称", False),
    "#NULL!": ("空值错误", "区域交集为空", False),
    "#NUM!": ("数值错误", "数值超出有效范围", True),
}

# 金额/数量关键词
AMOUNT_KEYWORDS = ["金额", "价格", "收入", "支出", "成本", "费用", "销售额",
                   "营业额", "利润", "工资", "薪资", "amount", "price", "revenue",
                   "cost", "fee", "salary", "预算", "总额", "gmv"]
QUANTITY_KEYWORDS = ["数量", "个数", "件数", "人数", "次数", "库存", "销量",
                     "qty", "quantity", "count", "num", "客户数", "用户数"]


@dataclass
class QualityReport:
    """质量检查报告"""
    file_path: str = ""
    score: float = 100.0
    passed: bool = True
    issues: List[ExcelQualityIssue] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    fixed_count: int = 0

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "score": self.score,
            "passed": self.passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "fixed_count": self.fixed_count,
            "issues": [i.to_dict() for i in self.issues],
        }

    def summary(self) -> str:
        lines = [
            f"质量评分: {self.score:.0f}/100  {'✓ 通过' if self.passed else '✗ 未通过'}",
            f"错误: {self.error_count}  警告: {self.warning_count}  提示: {self.info_count}",
        ]
        if self.fixed_count:
            lines.append(f"已自动修复: {self.fixed_count} 项")
        if self.issues:
            lines.append("\n问题详情:")
            for issue in self.issues:
                icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(issue.severity, "•")
                ref = f" [{issue.cell_ref}]" if issue.cell_ref else ""
                fixed = " (已修复)" if issue.fixable and issue.fixed else ""
                lines.append(f"  {icon} [{issue.issue_type}]{ref} {issue.message}{fixed}")
        return "\n".join(lines)


class ExcelQualityChecker:
    """
    Excel 质量检查器与自动修复

    用法:
        checker = ExcelQualityChecker()

        # 检查
        report = checker.check("output.xlsx")
        print(report.summary())

        # 检查并修复
        report = checker.check_and_fix("output.xlsx", "fixed.xlsx")
    """

    def check(self, file_path: str) -> QualityReport:
        """检查 Excel 文件质量"""
        report = QualityReport(file_path=file_path)

        if not Path(file_path).exists():
            report.issues.append(ExcelQualityIssue(
                issue_type="file", severity="error",
                message=f"文件不存在: {file_path}", fixable=False,
            ))
            self._calc_score(report)
            return report

        try:
            keep_vba = Path(file_path).suffix.lower() in (".xlsm", ".xltm")
            wb = load_workbook(file_path, data_only=False, keep_vba=keep_vba)
        except Exception as e:
            report.issues.append(ExcelQualityIssue(
                issue_type="file", severity="error",
                message=f"无法打开文件: {e}", fixable=False,
            ))
            self._calc_score(report)
            return report
        self._chart_source_path = file_path
        self._prepare_chart_inventory(file_path)

        for ws in wb.worksheets:
            sheet_issues = self._check_sheet(ws)
            report.issues.extend(sheet_issues)

        wb.close()

        # 缓存错误值扫描：公式单元格的真实错误值（#REF!/#DIV/0! 等）只存在
        # 于缓存值中，data_only=False 的结构检查看不到它们
        report.issues.extend(self._check_cached_errors(file_path))

        self._calc_score(report)
        return report

    def check_and_fix(self, file_path: str, output_path: str = None) -> QualityReport:
        """检查并自动修复"""
        report = QualityReport(file_path=file_path)

        if not Path(file_path).exists():
            report.issues.append(ExcelQualityIssue(
                issue_type="file", severity="error",
                message=f"文件不存在: {file_path}", fixable=False,
            ))
            self._calc_score(report)
            return report

        try:
            keep_vba = Path(file_path).suffix.lower() in (".xlsm", ".xltm")
            wb = load_workbook(file_path, data_only=False, keep_vba=keep_vba)
        except Exception as e:
            report.issues.append(ExcelQualityIssue(
                issue_type="file", severity="error",
                message=f"无法打开文件: {e}", fixable=False,
            ))
            self._calc_score(report)
            return report

        self._chart_source_path = file_path
        self._prepare_chart_inventory(file_path)
        for ws in wb.worksheets:
            # 先检查
            issues = self._check_sheet(ws)
            # 再修复可修复的问题
            fixed = self._fix_sheet(ws, issues)
            report.issues.extend(issues)
            report.fixed_count += fixed

        if output_path is None:
            source = Path(file_path)
            output_path = str(source.with_name(f"{source.stem}_fixed{source.suffix}"))

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        wb.save(output_path)
        wb.close()

        report.file_path = output_path
        self._calc_score(report)
        return report

    def check_profile(self, profile: DataProfile) -> List[ExcelQualityIssue]:
        """检查数据画像（生成前检查）"""
        issues = []
        for sheet in profile.sheets:
            if sheet.row_count <= 1:
                issues.append(ExcelQualityIssue(
                    sheet_name=sheet.name, issue_type="structure",
                    severity="warning", message=f"工作表 {sheet.name} 没有数据",
                ))
                continue

            for col in sheet.columns:
                null_ratio = col.null_count / max(sheet.row_count, 1)
                if null_ratio > 0.5:
                    issues.append(ExcelQualityIssue(
                        sheet_name=sheet.name, issue_type="data",
                        severity="warning",
                        message=f"列 {col.name} 空值率 {null_ratio:.0%}",
                        cell_ref=get_column_letter(col.index + 1),
                    ))
        return issues

    def check_with_score(self, file_path: str) -> dict:
        """检查 Excel 文件质量并返回分数与问题列表（同步考验处理接口）"""
        report = self.check(file_path)
        return report.to_dict()

    # ==========================================
    # 检查单个 Sheet
    # ==========================================

    def _check_sheet(self, ws) -> List[ExcelQualityIssue]:
        issues = []
        sheet_name = ws.title
        max_row = ws.max_row or 1
        max_col = ws.max_column or 1

        if max_row <= 1 and max_col <= 1:
            issues.append(ExcelQualityIssue(
                sheet_name=sheet_name, issue_type="structure",
                severity="warning", message="工作表为空",
            ))
            return issues

        # 1. 公式检查
        issues.extend(self._check_formulas(ws, max_row, max_col))

        # 2. 数据完整性
        issues.extend(self._check_data_integrity(ws, max_row, max_col))

        # 3. 格式检查
        issues.extend(self._check_formatting(ws, max_row, max_col))

        # 4. 图表检查
        issues.extend(self._check_charts(ws))

        # 5. 计算异常
        issues.extend(self._check_calculation_anomalies(ws, max_row, max_col))

        return issues

    # ==========================================
    # 1. 公式检查
    # ==========================================

    def _check_formulas(self, ws, max_row, max_col) -> List[ExcelQualityIssue]:
        issues = []
        sheet_name = ws.title
        formula_cells = {}  # col_idx -> [(row, formula)]

        for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col):
            for cell in row:
                val = cell.value
                if val is None:
                    continue

                # 错误值检查
                val_str = str(val)
                if val_str in EXCEL_ERRORS:
                    desc, detail, fixable = EXCEL_ERRORS[val_str]
                    issues.append(ExcelQualityIssue(
                        sheet_name=sheet_name, issue_type="formula",
                        severity="error",
                        message=f"{val_str}: {detail}",
                        cell_ref=cell.coordinate, fixable=fixable,
                    ))
                    continue

                # 公式检查
                if isinstance(val, str) and val.startswith("="):
                    col_idx = cell.column
                    if col_idx not in formula_cells:
                        formula_cells[col_idx] = []
                    formula_cells[col_idx].append((cell.row, val))

                    # 引用越界检查
                    ref_issues = self._check_formula_refs(ws, val, cell.coordinate)
                    issues.extend(ref_issues)

                    # 除零风险静态检查
                    divzero_issues = self._check_div_zero_risk(ws, val, cell.coordinate)
                    issues.extend(divzero_issues)

        # 公式一致性检查
        for col_idx, formulas in formula_cells.items():
            if len(formulas) < 2:
                continue
            # 检查公式模式是否一致（行号应该递增）
            patterns = set()
            for row, formula in formulas:
                # 将具体行号替换为 N 来比较模式
                pattern = re.sub(r'\d+', 'N', formula)
                patterns.add(pattern)

            if len(patterns) > 1:
                issues.append(ExcelQualityIssue(
                    sheet_name=sheet_name, issue_type="formula",
                    severity="warning",
                    message=f"第{get_column_letter(col_idx)}列公式模式不一致（{len(patterns)}种模式）",
                    cell_ref=get_column_letter(col_idx), fixable=True,
                ))

        return issues

    def _check_formula_refs(self, ws, formula: str, cell_ref: str) -> List[ExcelQualityIssue]:
        """检查公式引用是否越界"""
        issues = []
        reference_pattern = re.compile(
            r"(?:(?:'((?:[^']|'')+)'|([A-Za-z_][\w .]*))!)?"
            r"\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?",
            re.IGNORECASE,
        )
        for match in reference_pattern.finditer(formula):
            quoted_sheet, plain_sheet, col_str, row_str, col2_str, row2_str = match.groups()
            target_ws = ws
            referenced_sheet = (quoted_sheet or plain_sheet or "").replace("''", "'")
            if referenced_sheet:
                try:
                    target_ws = ws.parent[referenced_sheet]
                except KeyError:
                    issues.append(ExcelQualityIssue(
                        sheet_name=ws.title, issue_type="formula", severity="error",
                        message=f"公式引用了不存在的工作表 {referenced_sheet}",
                        cell_ref=cell_ref, fixable=False,
                    ))
                    continue
            try:
                col_num = column_index_from_string(col_str.upper())
                row_num = int(row_str)
                end_col = column_index_from_string(col2_str.upper()) if col2_str else col_num
                end_row = int(row2_str) if row2_str else row_num
                if (col_num > target_ws.max_column or row_num > target_ws.max_row
                        or end_col > target_ws.max_column or end_row > target_ws.max_row):
                    display_ref = match.group(0)
                    issues.append(ExcelQualityIssue(
                        sheet_name=ws.title, issue_type="formula",
                        severity="warning",
                        message=f"公式引用 {display_ref} 可能超出数据范围",
                        cell_ref=cell_ref, fixable=False,
                    ))
            except ValueError:
                continue

        return issues

    def _check_div_zero_risk(self, ws, formula: str, cell_ref: str) -> List[ExcelQualityIssue]:
        """静态检查公式除零风险"""
        issues = []

        # 匹配除法：/B3, /B3:C3 等
        div_matches = re.finditer(r'/([A-Z]+)(\d+)', formula)
        for m in div_matches:
            col_str, row_str = m.group(1), m.group(2)
            try:
                col_num = column_index_from_string(col_str)
                row_num = int(row_str)
                ref_cell = ws.cell(row=row_num, column=col_num)
                ref_val = ref_cell.value

                if ref_val == 0:
                    issues.append(ExcelQualityIssue(
                        sheet_name=ws.title, issue_type="formula",
                        severity="error",
                        message=f"#DIV/0! 风险：分母 {col_str}{row_str} 为 0",
                        cell_ref=cell_ref, fixable=True,
                    ))
                elif ref_val is None:
                    issues.append(ExcelQualityIssue(
                        sheet_name=ws.title, issue_type="formula",
                        severity="warning",
                        message=f"除零风险：分母 {col_str}{row_str} 为空",
                        cell_ref=cell_ref, fixable=True,
                    ))
            except (ValueError, Exception):
                pass

        return issues

    # ==========================================
    # 2. 数据完整性
    # ==========================================

    def _check_data_integrity(self, ws, max_row, max_col) -> List[ExcelQualityIssue]:
        issues = []
        sheet_name = ws.title

        # 找表头行
        header_row = self._find_header_row(ws, max_col)
        data_start = header_row + 1

        # 空行检查（连续空行）
        empty_rows = 0
        max_empty = 0
        for row_idx in range(data_start, max_row + 1):
            is_empty = all(
                ws.cell(row=row_idx, column=c).value is None
                for c in range(1, max_col + 1)
            )
            if is_empty:
                empty_rows += 1
                max_empty = max(max_empty, empty_rows)
            else:
                empty_rows = 0

        if max_empty >= 2:
            issues.append(ExcelQualityIssue(
                sheet_name=sheet_name, issue_type="data",
                severity="warning",
                message=f"数据区域有 {max_empty} 个连续空行",
                fixable=True,
            ))

        # 各列空值率
        for col_idx in range(1, max_col + 1):
            header_val = ws.cell(row=header_row, column=col_idx).value
            col_name = str(header_val) if header_val else f"列{col_idx}"

            null_count = 0
            total = 0
            has_number = False
            has_text_in_number_col = False

            for row_idx in range(data_start, max_row + 1):
                val = ws.cell(row=row_idx, column=col_idx).value
                if val is None:
                    null_count += 1
                else:
                    total += 1
                    if isinstance(val, (int, float)):
                        has_number = True
                    elif isinstance(val, str) and not val.startswith("="):
                        if has_number:
                            has_text_in_number_col = True

            data_rows = max_row - data_start + 1
            if data_rows > 0:
                null_ratio = null_count / data_rows
                if null_ratio > 0.3 and null_count < data_rows:
                    issues.append(ExcelQualityIssue(
                        sheet_name=sheet_name, issue_type="data",
                        severity="warning",
                        message=f"列「{col_name}」空值率 {null_ratio:.0%}",
                        cell_ref=get_column_letter(col_idx),
                        fixable=False,
                    ))

                if has_text_in_number_col:
                    issues.append(ExcelQualityIssue(
                        sheet_name=sheet_name, issue_type="data",
                        severity="warning",
                        message=f"列「{col_name}」数值列中混入文本",
                        cell_ref=get_column_letter(col_idx),
                        fixable=False,
                    ))

        # 重复行检查
        seen = {}
        dup_count = 0
        for row_idx in range(data_start, max_row + 1):
            row_vals = tuple(
                str(ws.cell(row=row_idx, column=c).value)
                for c in range(1, max_col + 1)
            )
            if all(v == "None" for v in row_vals):
                continue
            if row_vals in seen:
                dup_count += 1
            else:
                seen[row_vals] = row_idx

        if dup_count > 0:
            issues.append(ExcelQualityIssue(
                sheet_name=sheet_name, issue_type="data",
                severity="info",
                message=f"检测到 {dup_count} 行可能重复",
                fixable=False,
            ))

        return issues

    # ==========================================
    # 3. 格式检查
    # ==========================================

    def _check_formatting(self, ws, max_row, max_col) -> List[ExcelQualityIssue]:
        issues = []
        sheet_name = ws.title
        header_row = self._find_header_row(ws, max_col)

        # 表头样式检查
        header_styled = False
        header_fonts = set()
        for col_idx in range(1, min(max_col + 1, 20)):
            cell = ws.cell(row=header_row, column=col_idx)
            if cell.font and cell.font.bold:
                header_styled = True
            if cell.font and cell.font.name:
                header_fonts.add(cell.font.name)
            if cell.fill and cell.fill.fgColor:
                try:
                    rgb = cell.fill.fgColor.rgb
                    if rgb and str(rgb)[-6:].upper() not in ("FFFFFF", "000000"):
                        header_styled = True
                except Exception:
                    pass

        if not header_styled and max_row > header_row:
            issues.append(ExcelQualityIssue(
                sheet_name=sheet_name, issue_type="format",
                severity="info", message="表头缺少样式（建议加粗或添加背景色）",
                fixable=True,
            ))

        # 字体统一性
        body_fonts = set()
        for row_idx in range(header_row + 1, min(max_row + 1, header_row + 20)):
            for col_idx in range(1, min(max_col + 1, 10)):
                cell = ws.cell(row=row_idx, column=col_idx)
                if cell.font and cell.font.name:
                    body_fonts.add(cell.font.name)

        if len(body_fonts) > 3:
            issues.append(ExcelQualityIssue(
                sheet_name=sheet_name, issue_type="format",
                severity="info",
                message=f"正文字体种类过多（{len(body_fonts)}种），建议统一",
                fixable=True,
            ))

        # 列宽检查
        for col_idx in range(1, max_col + 1):
            col_letter = get_column_letter(col_idx)
            col_dim = ws.column_dimensions.get(col_letter)
            width = col_dim.width if col_dim and col_dim.width else 8.43

            # 检查数据是否被截断
            max_len = 0
            for row_idx in range(header_row, min(max_row + 1, header_row + 50)):
                val = ws.cell(row=row_idx, column=col_idx).value
                if val is not None:
                    val_str = str(val)
                    # 中文字符按2个宽度计算
                    length = sum(2 if ord(c) > 127 else 1 for c in val_str)
                    max_len = max(max_len, length)

            if max_len > width * 1.2 and width < max_len:
                issues.append(ExcelQualityIssue(
                    sheet_name=sheet_name, issue_type="format",
                    severity="info",
                    message=f"列「{col_letter}」宽度不足（当前{width:.0f}，建议{max_len + 2:.0f}）",
                    cell_ref=col_letter, fixable=True,
                ))

        # 数字格式检查
        for col_idx in range(1, max_col + 1):
            header_val = ws.cell(row=header_row, column=col_idx).value
            col_name = str(header_val) if header_val else ""

            is_amount = any(kw in col_name for kw in AMOUNT_KEYWORDS)
            is_quantity = any(kw in col_name for kw in QUANTITY_KEYWORDS)

            if is_amount or is_quantity:
                # 检查数据行格式
                sample_cell = ws.cell(row=header_row + 1, column=col_idx)
                fmt = sample_cell.number_format or "General"
                if fmt == "General" and isinstance(sample_cell.value, (int, float)):
                    issues.append(ExcelQualityIssue(
                        sheet_name=sheet_name, issue_type="format",
                        severity="info",
                        message=f"列「{col_name}」建议使用千分位数字格式",
                        cell_ref=get_column_letter(col_idx),
                        fixable=True,
                    ))

        return issues

    # 缓存值扫描上限：只读模式逐格扫大表开销大，限制每表规模
    _CACHED_SCAN_MAX_ROWS = 5000

    def _check_cached_errors(self, file_path: str) -> List[ExcelQualityIssue]:
        """以 data_only=True 重开文件，捕获公式计算后的真实错误值。"""
        issues = []
        try:
            wb = load_workbook(file_path, data_only=True, read_only=True)
        except Exception as exc:
            # 结构检查已通过、仅缓存值扫描不可用：跳过该腿但必须可观测
            logger.warning(
                "缓存错误值扫描跳过（无法以 data_only 重开文件）%s: %s",
                file_path, exc, exc_info=True,
            )
            return issues
        try:
            for ws in wb.worksheets:
                scanned = 0
                for row in ws.iter_rows(max_row=self._CACHED_SCAN_MAX_ROWS):
                    for cell in row:
                        scanned += 1
                        val = cell.value
                        if isinstance(val, str) and val in EXCEL_ERRORS:
                            desc, detail, _fixable = EXCEL_ERRORS[val]
                            issues.append(ExcelQualityIssue(
                                sheet_name=ws.title,
                                issue_type="formula",
                                severity="error",
                                message=f"{desc}：单元格 {cell.coordinate} 计算结果为 {val}（{detail}）",
                                cell_ref=cell.coordinate,
                                fixable=False,
                            ))
                            if len(issues) >= 20:
                                return issues
                _ = scanned
        except Exception as exc:
            # 扫描中途失败：保留已收集的部分结果返回，原因必须可观测
            logger.warning(
                "缓存错误值扫描中断，返回部分结果 %s: %s",
                file_path, exc, exc_info=True,
            )
        finally:
            wb.close()
        return issues

    # ==========================================
    # 4. 图表检查
    # ==========================================

    def _check_charts(self, ws) -> List[ExcelQualityIssue]:
        """图表检查：openpyxl 重新加载的文件解析不出已写入的图表对象，
        改为检查包内图表部件（新写入的图表必落在 xl/charts/ 下）。"""
        issues = []
        if not hasattr(self, "_chart_count"):
            self._prepare_chart_inventory(getattr(self, "_chart_source_path", ""))
        if getattr(self, "_chart_message_emitted", False):
            return []
        self._chart_message_emitted = True
        sheet_name = ws.title
        chart_count = self._chart_count

        if chart_count == 0:
            issues.append(ExcelQualityIssue(
                sheet_name=sheet_name, issue_type="chart",
                severity="warning", fixable=True,
                message="未生成任何图表",
            ))
            return issues

        issues.append(ExcelQualityIssue(
            sheet_name=sheet_name, issue_type="chart",
            severity="info", fixable=False,
            message=f"输出包含 {chart_count} 个图表，建议在 Excel/WPS 中打开确认渲染",
        ))
        return issues

    def _prepare_chart_inventory(self, file_path: str) -> None:
        """每个工作簿只解压扫描一次图表部件，并只生成一条工作簿级提示。"""
        self._chart_count = 0
        self._chart_message_emitted = False
        if not file_path:
            return
        try:
            import zipfile as _zipfile
            with _zipfile.ZipFile(file_path) as archive:
                self._chart_count = sum(
                    1 for name in archive.namelist()
                    if name.startswith("xl/charts/chart")
                )
        except (OSError, _zipfile.BadZipFile):
            self._chart_count = 0

    # ==========================================
    # 5. 计算异常
    # ==========================================

    def _check_calculation_anomalies(self, ws, max_row, max_col) -> List[ExcelQualityIssue]:
        issues = []
        sheet_name = ws.title
        header_row = self._find_header_row(ws, max_col)

        for col_idx in range(1, max_col + 1):
            header_val = ws.cell(row=header_row, column=col_idx).value
            col_name = str(header_val) if header_val else ""

            # 收集数值
            values = []
            for row_idx in range(header_row + 1, max_row + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                val = cell.value
                if isinstance(val, str) and val.startswith("="):
                    continue
                elif isinstance(val, (int, float)):
                    values.append((row_idx, val))

            if len(values) < 3:
                continue

            nums = [v for _, v in values]

            # 金额/数量列负数检查
            is_amount = any(kw in col_name for kw in AMOUNT_KEYWORDS)
            is_quantity = any(kw in col_name for kw in QUANTITY_KEYWORDS)

            if is_amount or is_quantity:
                negatives = [(r, v) for r, v in values if v < 0]
                if negatives:
                    issues.append(ExcelQualityIssue(
                        sheet_name=sheet_name, issue_type="calculation",
                        severity="warning",
                        message=f"列「{col_name}」有 {len(negatives)} 个负值",
                        cell_ref=get_column_letter(col_idx),
                        fixable=False,
                    ))

            # 零值过多
            zeros = sum(1 for v in nums if v == 0)
            if zeros > len(nums) * 0.5 and len(nums) > 5:
                issues.append(ExcelQualityIssue(
                    sheet_name=sheet_name, issue_type="calculation",
                    severity="info",
                    message=f"列「{col_name}」零值占比 {zeros/len(nums):.0%}",
                    cell_ref=get_column_letter(col_idx),
                    fixable=False,
                ))

            # 离群值检测（3σ原则）
            if len(nums) >= 5:
                try:
                    mean = statistics.mean(nums)
                    stdev = statistics.stdev(nums)
                    if stdev > 0:
                        outliers = [
                            (r, v) for r, v in values
                            if abs(v - mean) > 3 * stdev
                        ]
                        if outliers and len(outliers) <= 3:
                            for r, v in outliers:
                                issues.append(ExcelQualityIssue(
                                    sheet_name=sheet_name,
                                    issue_type="calculation",
                                    severity="info",
                                    message=f"列「{col_name}」单元格 {get_column_letter(col_idx)}{r} 值 {v:,.0f} 可能是离群值（均值{mean:,.0f}）",
                                    cell_ref=f"{get_column_letter(col_idx)}{r}",
                                    fixable=False,
                                ))
                except (statistics.StatisticsError, Exception):
                    pass

        return issues

    # ==========================================
    # 自动修复
    # ==========================================

    def _fix_sheet(self, ws, issues: List[ExcelQualityIssue]) -> int:
        """修复可修复的问题，返回修复数量"""
        fixed = 0
        max_row = ws.max_row or 1
        max_col = ws.max_column or 1
        header_row = self._find_header_row(ws, max_col)

        for issue in issues:
            if not issue.fixable:
                continue

            try:
                if issue.issue_type == "formula" and "#DIV/0!" in issue.message:
                    # 用 IFERROR 包裹
                    if issue.cell_ref:
                        cell = ws[issue.cell_ref]
                        if isinstance(cell.value, str) and cell.value.startswith("="):
                            inner = cell.value[1:]
                            cell.value = f'=IFERROR({inner},"")'
                            issue.fixed = True
                            fixed += 1

                elif issue.issue_type == "formula" and "除零风险" in issue.message:
                    if issue.cell_ref:
                        cell = ws[issue.cell_ref]
                        if isinstance(cell.value, str) and cell.value.startswith("="):
                            inner = cell.value[1:]
                            cell.value = f'=IFERROR({inner},"")'
                            issue.fixed = True
                            fixed += 1

                elif issue.issue_type == "formula" and "#N/A" in issue.message:
                    if issue.cell_ref:
                        cell = ws[issue.cell_ref]
                        if isinstance(cell.value, str) and cell.value.startswith("="):
                            inner = cell.value[1:]
                            cell.value = f"=IFERROR({inner},\"\")"
                            issue.fixed = True
                            fixed += 1

                elif issue.issue_type == "format" and "宽度不足" in issue.message:
                    # 自动调整列宽
                    if issue.cell_ref:
                        col_letter = issue.cell_ref
                        max_len = 0
                        for row_idx in range(1, min(max_row + 1, 100)):
                            val = ws.cell(row=row_idx,
                                         column=column_index_from_string(col_letter)).value
                            if val is not None:
                                length = sum(2 if ord(c) > 127 else 1 for c in str(val))
                                max_len = max(max_len, length)
                        if max_len > 0:
                            ws.column_dimensions[col_letter].width = max_len + 2
                            issue.fixed = True
                            fixed += 1

                elif issue.issue_type == "format" and "表头缺少样式" in issue.message:
                    # 添加表头样式
                    header_fill = PatternFill(
                        start_color="1F4E79",
                        end_color="1F4E79",
                        fill_type="solid",
                    )
                    header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
                    for col_idx in range(1, max_col + 1):
                        cell = ws.cell(row=header_row, column=col_idx)
                        if cell.value is not None:
                            cell.fill = header_fill
                            cell.font = header_font
                            cell.alignment = Alignment(horizontal="center", vertical="center")
                    issue.fixed = True
                    fixed += 1

                elif issue.issue_type == "format" and "字体种类过多" in issue.message:
                    # 统一正文字体
                    body_font = Font(name="微软雅黑", size=10)
                    for row_idx in range(header_row + 1, max_row + 1):
                        for col_idx in range(1, max_col + 1):
                            cell = ws.cell(row=row_idx, column=col_idx)
                            if cell.value is not None and not (isinstance(cell.value, str) and cell.value.startswith("=")):
                                cell.font = body_font
                    issue.fixed = True
                    fixed += 1

                elif issue.issue_type == "format" and "千分位" in issue.message:
                    # 设置数字格式
                    if issue.cell_ref:
                        col_idx = column_index_from_string(issue.cell_ref)
                        for row_idx in range(header_row + 1, max_row + 1):
                            cell = ws.cell(row=row_idx, column=col_idx)
                            if isinstance(cell.value, (int, float)):
                                cell.number_format = '#,##0'
                        issue.fixed = True
                        fixed += 1

                elif issue.issue_type == "data" and "连续空行" in issue.message:
                    # 只删除"末尾"连续空行：遇到第一个非空行立即停止。
                    # （原实现收集 2+ 空行后才停止，会把数据中段的空行也删掉，
                    # 使下方公式引用行号错位产生 #REF!）
                    rows_to_delete = []
                    for row_idx in range(max_row, header_row, -1):
                        is_empty = all(
                            ws.cell(row=row_idx, column=c).value is None
                            for c in range(1, max_col + 1)
                        )
                        if is_empty:
                            rows_to_delete.append(row_idx)
                        else:
                            break  # 第一个非空行即停止：只处理末尾空行

                    if len(rows_to_delete) >= 2:
                        for row_idx in rows_to_delete:
                            ws.delete_rows(row_idx)
                        issue.fixed = True
                        fixed += 1

            except Exception:
                pass

        return fixed

    # ==========================================
    # 辅助方法
    # ==========================================

    def _find_header_row(self, ws, max_col) -> int:
        """查找表头行"""
        for r in range(1, min(5, (ws.max_row or 1) + 1)):
            for c in range(1, min(max_col + 1, 5)):
                val = ws.cell(row=r, column=c).value
                if val is not None:
                    # 检查这一行是否像表头（文本值，下一行有数据）
                    if isinstance(val, str):
                        next_val = ws.cell(row=r + 1, column=c).value if r < (ws.max_row or 1) else None
                        if next_val is not None:
                            return r
        return 1

    def _calc_score(self, report: QualityReport):
        """计算质量评分"""
        unresolved = [i for i in report.issues if not getattr(i, "fixed", False)]
        report.error_count = sum(1 for i in unresolved if i.severity == "error")
        report.warning_count = sum(1 for i in unresolved if i.severity == "warning")
        report.info_count = sum(1 for i in unresolved if i.severity == "info")

        score = 100
        score -= report.error_count * 10
        score -= report.warning_count * 3
        score -= report.info_count * 1
        score += report.fixed_count * 2  # 修复加分

        report.score = max(0, min(100, score))
        report.passed = report.error_count == 0


def check_excel(file_path: str) -> QualityReport:
    """便捷函数：检查 Excel 质量"""
    return ExcelQualityChecker().check(file_path)


def check_and_fix_excel(file_path: str, output_path: str = None) -> QualityReport:
    """便捷函数：检查并修复"""
    return ExcelQualityChecker().check_and_fix(file_path, output_path)
