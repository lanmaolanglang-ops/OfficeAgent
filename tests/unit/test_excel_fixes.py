"""
Excel 处理链修复的回归测试

覆盖：
- 含空格 sheet 名的图表生成（Reference 引号包裹）
- 图表数据范围不含类别列
- CSV 多编码读取 + 公式注入防护
- 占比公式自包含 SUM
- save() 拒绝覆写打开的输入文件
- NaN/Inf 消毒
- 便捷公式列偏移正确
- header/label 规格写文本而非公式
- 往返脆弱元素警示
- 缓存错误值扫描
"""
import sys
import zipfile
from pathlib import Path

import openpyxl
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from office_agent.excel_agent.excel_orchestrator import ExcelOrchestrator  # noqa: E402
from office_agent.excel_agent.excel_service import ExcelService  # noqa: E402
from office_agent.excel_agent.quality_checker import ExcelQualityChecker  # noqa: E402
from office_agent.excel_agent.data_analyzer import DataAnalyzer  # noqa: E402
from office_agent.excel_agent.formula_generator import FormulaGenerator  # noqa: E402
from office_agent.excel_agent.analysis_engine import AnalysisEngine  # noqa: E402
from office_agent.excel_agent.models import (  # noqa: E402
    FormulaSpec, ColumnInfo, SheetInfo, DataProfile,
)
from office_agent.task_queue.tasks.excel_tasks import (  # noqa: E402
    _csv_to_xlsx, _read_csv_any_encoding,
)


def _workbook_with_spaced_sheet(path: Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "销售 数据"
    ws.append(["月份", "销售额", "成本"])
    for row in [("1月", 100, 60), ("2月", 150, 70), ("3月", 130, 65)]:
        ws.append(row)
    wb.save(str(path))


class TestSpacedSheetCharts:
    def test_process_file_succeeds(self, temp_dir):
        """含空格 sheet 名的文件处理不应崩溃（Reference 需引号包裹）"""
        src = temp_dir / "in.xlsx"
        out = temp_dir / "out.xlsx"
        _workbook_with_spaced_sheet(src)

        result = ExcelOrchestrator().process_file(str(src), task="生成销售额柱状图并计算合计",
                                                  output_path=str(out))
        assert result.success, result.message

    def test_chart_excludes_category_column(self, temp_dir):
        """图表数据引用不得包含类别列（否则饼图必然画错）"""
        import re
        src = temp_dir / "in.xlsx"
        out = temp_dir / "out.xlsx"
        _workbook_with_spaced_sheet(src)

        result = ExcelOrchestrator().process_file(str(src), task="生成销售额柱状图",
                                                  output_path=str(out),
                                                  add_charts=True)
        assert result.success

        with zipfile.ZipFile(str(out)) as z:
            chart_xmls = [n for n in z.namelist() if n.startswith("xl/charts/chart")]
        assert chart_xmls, "输出应包含图表"
        for name in chart_xmls:
            with zipfile.ZipFile(str(out)) as z:
                xml = z.read(name).decode("utf-8")
            for ref in re.findall(r"<c:f>([^<]+)</c:f>", xml):
                letters = re.findall(r"\$?([A-Z]+)\$?\d*", ref.split("!")[-1])
                assert "A" not in letters, f"类别列泄漏进图表数据引用: {ref}"


class TestCsvHandling:
    def test_gbk_read_and_bom_free_headers(self, temp_dir):
        csv_path = temp_dir / "gbk.csv"
        with open(csv_path, "w", encoding="gbk", newline="") as f:
            f.write("产品,备注\nA,正常\n")
        df = _read_csv_any_encoding(csv_path)
        assert list(df.columns) == ["产品", "备注"]

    def test_formula_injection_sanitized(self, temp_dir):
        csv_path = temp_dir / "inject.csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write("产品,备注,数量\n")
            f.write("A,=cmd|' /C calc'!A0,5\n")
            f.write("B,正常,-3\n")
        tmp = _csv_to_xlsx(str(csv_path))
        try:
            ws = openpyxl.load_workbook(tmp).active
            assert not str(ws.cell(row=2, column=2).value).startswith("=")
            assert ws.cell(row=3, column=3).value == -3, "负数不应被当作注入"
        finally:
            Path(tmp).unlink(missing_ok=True)


class TestPercentFormula:
    def test_self_contained_sum(self, temp_dir):
        """占比公式分母用 SUM(数据区)，不依赖外部合计行"""
        src = temp_dir / "pct_in.xlsx"
        out = temp_dir / "pct_out.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "数据"
        ws.append(["产品", "销售额"])
        ws.append(["甲", 100])
        ws.append(["乙", 300])
        wb.save(str(src))

        result = ExcelOrchestrator().process_file(
            str(src), task="计算各产品销售额占比", output_path=str(out), add_charts=False)
        assert result.success, result.message

        ws_out = openpyxl.load_workbook(str(out))["数据"]
        formulas = [c.value for row in ws_out.iter_rows() for c in row
                    if isinstance(c.value, str) and "SUM(" in c.value]
        assert formulas, "应写入占比公式"
        assert all("SUM(" in f for f in formulas)


class TestSaveGuard:
    def test_refuses_overwrite_of_opened_input(self, temp_dir):
        src = temp_dir / "x.xlsx"
        wb = openpyxl.Workbook()
        wb.active["A1"] = "原始数据"
        wb.save(str(src))

        svc = ExcelService()
        svc.open(str(src))
        res = svc.save(str(src))
        assert not res.success and "拒绝保存" in res.message
        # 原文件未被破坏
        assert openpyxl.load_workbook(str(src)).active["A1"].value == "原始数据"

    def test_create_save_to_same_path_allowed(self, temp_dir):
        """create() 新建工作簿保存到同一路径是合法操作"""
        path = temp_dir / "new.xlsx"
        svc = ExcelService()
        svc.create(str(path))
        svc.write_data("Sheet1", [["a", 1]])
        assert svc.save(str(path)).success


class TestDataSanitizing:
    def test_nan_inf_become_none(self, temp_dir):
        out = temp_dir / "nan.xlsx"
        svc = ExcelService()
        svc.create(str(out))
        svc.write_data("Sheet1", [["a", float("nan"), float("inf")], ["b", 1, 2]])
        svc.save(str(out))
        cell = openpyxl.load_workbook(str(out)).active.cell(row=1, column=2)
        assert cell.value is None


class TestFormulaHelpers:
    def test_summary_column_offset(self, temp_dir):
        src = temp_dir / "pct_in.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "数据"
        ws.append(["产品", "销售额"])
        ws.append(["甲", 100])
        wb.save(str(src))

        prof = DataAnalyzer().analyze(str(src))
        specs = FormulaGenerator(prof).generate_summary_row(prof.get_sheet("数据"),
                                                            sum_cols=[1])
        assert specs[0].target_cell.startswith("B")
        assert "B2:B" in specs[0].formula

    def test_header_spec_written_as_text(self, temp_dir):
        out = temp_dir / "txt.xlsx"
        svc = ExcelService()
        svc.create(str(out))
        svc.add_formula(FormulaSpec(formula="环比增长率", target_cell="C1", category="header"))
        svc.save(str(out))
        cell = openpyxl.load_workbook(str(out)).active["C1"]
        assert cell.value == "环比增长率"
        assert cell.data_type != "f"

    def test_sumif_escapes_double_quotes(self):
        category = ColumnInfo(
            name="分类", index=0, data_type="text", semantic_type="category",
            unique_values=['A"类'],
        )
        amount = ColumnInfo(
            name="金额", index=1, data_type="number", semantic_type="amount",
        )
        sheet = SheetInfo(name="数据", row_count=3, col_count=2,
                          columns=[category, amount])
        profile = DataProfile(sheets=[sheet])
        specs = FormulaGenerator(profile).generate_from_text("按分类汇总金额", "数据")
        assert any('"A""类"' in spec.formula for spec in specs)

    def test_yoy_uses_previous_year_period_not_previous_row(self):
        amount = ColumnInfo(
            name="销售额", index=0, data_type="number", semantic_type="amount",
        )
        sheet = SheetInfo(name="月报", row_count=15, col_count=1, columns=[amount])
        profile = DataProfile(sheets=[sheet])
        specs = FormulaGenerator(profile).generate_from_text("计算销售额同比增长", "月报")
        formulas = [spec for spec in specs if spec.category == "growth_yoy"]
        assert formulas
        assert formulas[0].target_cell == "B14"
        assert "A14-A2" in formulas[0].formula


class TestSortAndSummary:
    def test_sort_preserves_formula_style_and_column_width(self, temp_dir):
        src = temp_dir / "sort.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "数据"
        ws.append(["名称", "值", "计算"])
        ws.append(["B", 2, "=B2*2"])
        ws.append(["A", 1, "=B3*2"])
        ws["C2"].fill = openpyxl.styles.PatternFill("solid", fgColor="FF0000")
        ws["C3"].fill = openpyxl.styles.PatternFill("solid", fgColor="00FF00")
        ws.column_dimensions["C"].width = 27
        wb.save(src)

        svc = ExcelService().open(str(src))
        svc.sort_data("数据", 0)
        sorted_ws = svc.get_sheet("数据")
        assert sorted_ws["A2"].value == "A"
        assert sorted_ws["C2"].value == "=B2*2"
        assert sorted_ws["C2"].fill.fgColor.rgb.endswith("00FF00")
        assert sorted_ws.column_dimensions["C"].width == 27

    def test_summary_respects_start_row_and_does_not_duplicate(self, temp_dir):
        svc = ExcelService().create(str(temp_dir / "summary.xlsx"), "数据")
        ws = svc.get_sheet("数据")
        ws.append(["报表标题", None])
        ws.append(["单位：万元", None])
        ws.append(["项目", "金额"])
        ws.append(["甲", 10])
        ws.append(["乙", 20])
        svc.add_summary_row("数据", sum_cols=[1], data_start_row=4)
        svc.add_summary_row("数据", sum_cols=[1], data_start_row=4)
        assert ws.max_row == 6
        assert ws["B6"].value == "=SUM(B4:B5)"


class TestQualityChecker:
    def test_fragile_element_warning(self, temp_dir):
        from openpyxl.chart import BarChart, Reference
        src = temp_dir / "chart_in.xlsx"
        out = temp_dir / "chart_out.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "数据"
        ws.append(["月", "值"])
        ws.append(["a", 1])
        ch = BarChart()
        ch.add_data(Reference(ws, min_col=2, min_row=1, max_row=2), titles_from_data=True)
        ws.add_chart(ch, "E2")
        wb.save(str(src))

        result = ExcelOrchestrator().process_file(str(src), task="计算合计",
                                                  output_path=str(out), add_charts=False)
        assert result.success
        assert "图表" in result.message

    def test_cached_error_scan(self, temp_dir):
        src = temp_dir / "err.xlsx"
        wb = openpyxl.Workbook()
        wb.active["A1"] = "#REF!"
        wb.save(str(src))
        issues = ExcelQualityChecker()._check_cached_errors(str(src))
        assert any("#REF!" in i.message for i in issues)

    def test_chart_part_detection(self, temp_dir):
        from openpyxl.chart import BarChart, Reference
        src = temp_dir / "c.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["月", "值"])
        ws.append(["a", 1])
        ch = BarChart()
        ch.add_data(Reference(ws, min_col=2, min_row=1, max_row=2), titles_from_data=True)
        ws.add_chart(ch, "E2")
        wb.save(str(src))

        checker = ExcelQualityChecker()
        checker._chart_source_path = str(src)
        issues = checker._check_charts(ws)
        assert any("1 个图表" in i.message for i in issues)

    def test_fragile_detection_reuses_loaded_workbook(self, temp_dir, monkeypatch):
        """传入已加载工作簿时不得再整本 load_workbook（重复磁盘解析）。"""
        from openpyxl.chart import BarChart, Reference
        src = temp_dir / "reuse.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["月", "值"])
        ws.append(["a", 1])
        ch = BarChart()
        ch.add_data(Reference(ws, min_col=2, min_row=1, max_row=2), titles_from_data=True)
        ws.add_chart(ch, "E2")
        wb.save(str(src))

        loaded = openpyxl.load_workbook(str(src))

        def _boom(*_args, **_kwargs):
            raise AssertionError("不应再次 load_workbook")

        monkeypatch.setattr(openpyxl, "load_workbook", _boom)
        try:
            result = ExcelOrchestrator._detect_fragile_elements(str(src), wb=loaded)
        finally:
            loaded.close()
        assert "图表" in result

    def test_fragile_detection_falls_back_to_loading(self, temp_dir):
        """不传工作簿时保持旧行为：自行打开文件检测。"""
        from openpyxl.chart import BarChart, Reference
        src = temp_dir / "fallback.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["月", "值"])
        ws.append(["a", 1])
        ch = BarChart()
        ch.add_data(Reference(ws, min_col=2, min_row=1, max_row=2), titles_from_data=True)
        ws.add_chart(ch, "E2")
        wb.save(str(src))

        result = ExcelOrchestrator._detect_fragile_elements(str(src))
        assert "图表" in result


class TestRemainingExcelCorrectness:
    def test_orchestrator_processes_every_sheet_and_every_numeric_column(self, temp_dir):
        src = temp_dir / "multi.xlsx"
        out = temp_dir / "multi_out.xlsx"
        wb = openpyxl.Workbook()
        for index, name in enumerate(("一部", "二部")):
            ws = wb.active if index == 0 else wb.create_sheet()
            ws.title = name
            ws.append(["项目", "一月", "二月", "三月", "四月"])
            ws.append(["甲", 1, 2, 3, 4])
            ws.append(["乙", 5, 6, 7, 8])
        wb.save(src)

        result = ExcelOrchestrator().process_file(
            str(src), output_path=str(out), task="", add_charts=False,
        )
        assert result.success, result.message
        generated = openpyxl.load_workbook(out)
        for name in ("一部", "二部"):
            ws = generated[name]
            assert ws["A4"].value == "合计"
            assert len(ws.conditional_formatting) == 4

    def test_unknown_conditional_format_type_fails_closed(self):
        """P5-10：未知条件格式不能静默 no-op 后伪造成功变更记录。"""
        service = ExcelService().create("unused.xlsx")
        with pytest.raises(ValueError, match="不支持的条件格式类型"):
            service.add_conditional_format("Sheet1", "A1:A2", "gradient")
        assert not any("gradient" in change for change in service.changes)

    def test_formula_and_summary_are_not_mutually_exclusive(self, temp_dir):
        src = temp_dir / "summary.xlsx"
        out = temp_dir / "summary_out.xlsx"
        _workbook_with_spaced_sheet(src)
        result = ExcelOrchestrator().process_file(
            str(src), task="计算销售额求和并汇总", output_path=str(out), add_charts=False,
        )
        assert result.success, result.message
        assert any("添加汇总行" in change for change in result.changes)
        assert any("公式" in change for change in result.changes)

    def test_create_from_empty_data_and_explicit_data_only_mode(self, temp_dir):
        empty = ExcelOrchestrator().create_from_data(
            [], output_path=str(temp_dir / "empty.xlsx"), headers=[],
        )
        assert empty.success, empty.message

        out = temp_dir / "rows.xlsx"
        result = ExcelOrchestrator().create_from_data(
            [["甲", 1], ["乙", 2]], has_header=False, output_path=str(out),
        )
        assert result.success, result.message
        ws = openpyxl.load_workbook(out).active
        assert ws["A1"].value == "列1"
        assert ws["A2"].value == "甲"

    def test_analysis_handles_all_sheets_and_precise_time_headers(self, temp_dir):
        src = temp_dir / "analysis.xlsx"
        wb = openpyxl.Workbook()
        for index, name in enumerate(("甲", "乙")):
            ws = wb.active if index == 0 else wb.create_sheet()
            ws.title = name
            ws.append(["名称", "金额"])
            ws.append(["A", 1])
            ws.append(["B", 2])
        wb.save(src)

        engine = AnalysisEngine()
        report = engine.analyze_file(str(src))
        assert report.sheet_name == "全部工作表"
        assert [item.sheet_name for item in report.sheet_reports] == ["甲", "乙"]
        assert engine._find_time_column(["名称", "期货价格"], [], [0, 1]) == 0
        assert engine._find_time_column(["名称", "周期"], [], [0, 1]) == 1

    def test_analysis_uses_schema_for_the_current_sheet(self):
        from office_agent.excel_agent.models import ColumnInfo, DataSchema, SheetSchema

        engine = AnalysisEngine()
        engine.schema = DataSchema(sheets=[
            SheetSchema(name="甲", columns=[
                ColumnInfo("名称", 0), ColumnInfo("金额", 1, unit="元"),
            ]),
            SheetSchema(name="乙", columns=[
                ColumnInfo("名称", 0), ColumnInfo("金额", 1, unit="万元"),
            ]),
        ])
        analysis = engine._analyze_column(
            [["A", 2]], ["名称", "金额"], 1, [0], sheet_name="乙"
        )
        assert analysis.unit == "万元"

    def test_iqr_uses_tukey_hinges_for_small_samples(self):
        values = [1, 2, 3, 100]
        rows = [["a", 1], ["b", 2], ["c", 3], ["d", 100]]
        outliers = AnalysisEngine()._detect_outliers_iqr(values, rows, 1, 0)
        assert outliers == [{"value": 100.0, "label": "d", "type": "high"}]

    def test_formula_refs_validate_local_and_cross_sheet_ranges(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "当前"
        ws.append(["值"])
        ws.append([1])
        other = wb.create_sheet("其他")
        other.append(["值"])
        other.append([2])
        issues = ExcelQualityChecker()._check_formula_refs(
            ws, "='其他'!A99+A88", "B2"
        )
        messages = [issue.message for issue in issues]
        assert any("'其他'!A99" in message for message in messages)
        assert any("A88" in message for message in messages)

    def test_trend_thresholds_share_named_constants(self):
        """趋势方向（3%）与重要发现（5%）阈值收敛为同一组命名常量。"""
        from office_agent.excel_agent import analysis_engine as ae

        engine = AnalysisEngine()
        # 平均环比 4%：超过方向阈值 → up；但未达重要发现阈值
        rows = [["p1", 100], ["p2", 104], ["p3", 108.16]]
        ta = engine._analyze_trend(rows, ["期间", "金额"], 1, 0)
        assert ta.trend == "up"
        assert abs(ta.avg_growth_rate - 0.04) < 1e-9
        assert (ae.TREND_DIRECTION_THRESHOLD
                < ta.avg_growth_rate
                < ae.TREND_FINDING_THRESHOLD)

        # 低于方向阈值的噪声（2%）不应被命名为上涨
        rows = [["p1", 100], ["p2", 102], ["p3", 104.04]]
        ta = engine._analyze_trend(rows, ["期间", "金额"], 1, 0)
        assert ta.trend != "up"

    def test_analyzer_fallback_logs_reason(self, temp_dir, caplog, monkeypatch):
        """pandas 解析失败降级 openpyxl 时必须留下结构化日志，不能静默。"""
        import logging

        import pandas as pd

        src = temp_dir / "fallback.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["名称", "金额"])
        ws.append(["A", 1])
        wb.save(src)

        def _boom(*_args, **_kwargs):
            raise ValueError("forced pandas failure")

        monkeypatch.setattr(pd, "ExcelFile", _boom)
        with caplog.at_level(
            logging.WARNING, logger="office_agent.excel_agent.data_analyzer"
        ):
            profile = DataAnalyzer().analyze(str(src))
        assert profile is not None
        assert any("降级" in record.message for record in caplog.records)

    def test_chart_notice_is_workbook_level_and_div_zero_fix_is_blank(self, temp_dir):
        src = temp_dir / "book.xlsx"
        wb = openpyxl.Workbook()
        wb.active.title = "一"
        wb.active.append(["值"])
        wb.active.append([1])
        second = wb.create_sheet("二")
        second.append(["值"])
        second.append([2])
        wb.save(src)
        report = ExcelQualityChecker().check(str(src))
        chart_issues = [issue for issue in report.issues if issue.issue_type == "chart"]
        assert len(chart_issues) == 1

        ws = wb["一"]
        ws["A2"] = 0
        ws["B2"] = "=1/A2"
        issue = ExcelQualityChecker()._check_div_zero_risk(ws, ws["B2"].value, "B2")[0]
        ExcelQualityChecker()._fix_sheet(ws, [issue])
        assert ws["B2"].value == '=IFERROR(1/A2,"")'
