"""P2-38 回归：字段名含「年/月/日」不得把数值列误判为日期。"""
from __future__ import annotations

import pandas as pd
import pytest

from office_agent.excel_agent.data_analyzer import DataAnalyzer


def _infer(name: str, values: list, data_type: str | None = None) -> str:
    analyzer = DataAnalyzer()
    series = pd.Series(values)
    if data_type is None:
        data_type = analyzer._infer_data_type(series, name)
    unique_ratio = series.nunique(dropna=True) / max(len(series), 1)
    return analyzer._infer_semantic_type(series, name, data_type, unique_ratio)


class TestDateSemanticNotOverriddenByShortKeywords:
    @pytest.mark.parametrize("name,values", [
        ("月度金额", [1200.5, 2300.0, 1800.25]),
        ("月销售额", [100, 200, 300]),
        ("年收入", [1000000, 1200000]),
        ("年度成本", [50000, 60000]),
        ("日均销量", [12, 34, 56]),
        ("日销售金额", [999.0, 1200.5]),
        ("月费用", [300, 400]),
        ("年利润", [80000, 90000]),
    ])
    def test_numeric_fields_with_month_year_day_are_not_date(self, name, values):
        result = _infer(name, values, data_type="number")
        assert result != "date", f"{name} 被误判为 date"
        assert result in {"amount", "quantity", "metric", "percentage"}

    @pytest.mark.parametrize("name,values,expected_any", [
        ("日期", ["2026-09-01", "2026-09-02"], {"date"}),
        ("交易日期", ["2026-01-01", "2026-02-01"], {"date"}),
        ("创建时间", ["2026-03-01 10:00:00"], {"date"}),
        ("年月", ["2026-01", "2026-02"], {"date"}),
        ("月份", ["1月", "2月", "3月"], {"date"}),
        ("order_date", ["2026-09-01"], {"date"}),
    ])
    def test_real_date_columns_still_recognized(self, name, values, expected_any):
        series = pd.Series(values)
        analyzer = DataAnalyzer()
        data_type = analyzer._infer_data_type(series, name)
        unique_ratio = 1.0
        result = analyzer._infer_semantic_type(series, name, data_type, unique_ratio)
        # 可能 data_type 已是 date，或语义关键词命中 date
        assert result in expected_any or data_type == "date", (
            name, data_type, result
        )

    def test_datetime_dtype_is_date(self):
        series = pd.Series(pd.to_datetime(["2026-01-01", "2026-02-01"]))
        analyzer = DataAnalyzer()
        data_type = analyzer._infer_data_type(series, "创建时间")
        assert data_type == "date"
        assert analyzer._infer_semantic_type(series, "创建时间", data_type, 1.0) == "date"

    def test_longest_keyword_wins(self):
        # 「金额」比「月」长，必须判 amount
        assert _infer("月度金额", [1.0, 2.0], "number") == "amount"
        # 「销量」比「日」长
        assert _infer("日均销量", [1, 2, 3], "number") in {"quantity", "metric"}


class TestSchemaLevelInference:
    def test_openpyxl_profile_numeric_headers(self, tmp_path):
        from openpyxl import Workbook

        from office_agent.excel_agent.data_analyzer import DataAnalyzer

        path = tmp_path / "sales.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["日期", "月度金额", "年收入", "日均销量"])
        ws.append(["2026-01-01", 1000, 50000, 10])
        ws.append(["2026-02-01", 2000, 60000, 20])
        wb.save(str(path))

        profile = DataAnalyzer().analyze(str(path))
        cols = {c.name: c for sheet in profile.sheets for c in sheet.columns}
        assert "月度金额" in cols
        assert cols["月度金额"].semantic_type != "date"
        assert cols["年收入"].semantic_type != "date"
        assert cols["日均销量"].semantic_type != "date"
