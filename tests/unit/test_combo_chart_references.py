"""Combo Chart 显式数据引用建模专项测试（清单 2.6 图表生成节两项同根因）。

根因：combo 图的引用曾建模为单个连续矩形 ``data_range``
（类别列 + 连续数据列），同时产生两个假设：
- 类别列必须在最左侧；
- 数据列必须连续相邻。
修复：``_build_combo_spec`` 填充 ``series_ranges``（每系列真实列
引用，含表头），``_render_combo`` 优先消费 series_ranges 与显式
categories_range；无 series_ranges 的旧 ChartSpec 仍走矩形拆分，
行为不变。

本文件验证的是**最终 chart reference**：组合图在 openpyxl 中由
``chart._charts``（[BarChart, LineChart]）承载；每个系列的
``val.numRef.f`` 指向数据区（titles_from_data=True 时表头行进入
``tx.strRef.f``），``cat`` 引用类别区。
"""
from openpyxl import Workbook

from office_agent.excel_agent.chart_generator import ChartGenerator
from office_agent.excel_agent.models import (
    ChartSpec,
    ColumnInfo,
    DataProfile,
    SheetInfo,
)


def _profile(columns, row_count=3):
    """columns: [(name, index, data_type), ...]，0-based index。"""
    sheet = SheetInfo(
        name="Sheet1",
        row_count=row_count,
        col_count=len(columns),
        columns=[ColumnInfo(name=n, index=i, data_type=t) for n, i, t in columns],
    )
    return DataProfile(file_path="test.xlsx", sheets=[sheet])


def _ws_with_data(ncols=8, nrows=4):
    """带表头 + 3 行数据的工作表（列数足够覆盖 G 列）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for c in range(1, ncols + 1):
        ws.cell(row=1, column=c, value=f"col{c}")
        for r in range(2, nrows + 1):
            ws.cell(row=r, column=c, value=r * c)
    return ws


def _render(spec):
    gen = ChartGenerator()
    ws = _ws_with_data()
    gen._render_combo(ws, spec)
    assert len(ws._charts) == 1
    return ws._charts[0]


def _sub_charts(chart):
    """组合图的子图列表（未组合时为 [chart] 自身）。"""
    return getattr(chart, "_charts", [chart])


def _series_value_refs(chart):
    """每个系列的数值引用公式（按柱→线顺序，数据区不含表头）。"""
    return [s.val.numRef.f for sub in _sub_charts(chart) for s in sub.series]


def _series_title_refs(chart):
    """每个系列的标题引用（表头单元格，证明 titles_from_data 生效）。"""
    return [
        (s.tx.strRef.f if s.tx and s.tx.strRef else None)
        for sub in _sub_charts(chart)
        for s in sub.series
    ]


def _category_ref(chart):
    cat = chart.series[0].cat
    ref = cat.numRef if cat.numRef is not None else cat.strRef
    return ref.f


def _build(columns, cat_index, target_indexes, row_count=3):
    gen = ChartGenerator(profile=_profile(columns, row_count))
    sheet = gen.profile.sheets[0]
    cat_col = next(c for c in sheet.columns if c.index == cat_index)
    target_cols = [c for c in sheet.columns if c.index in target_indexes]
    spec = gen._build_combo_spec(target_cols, cat_col, sheet, "")
    assert spec is not None
    return spec


# ---------------------------------------------------------------
# 构建侧：series_ranges 指向每列真实引用
# ---------------------------------------------------------------

def test_build_spec_emits_explicit_series_ranges():
    spec = _build(
        [("销量", 0, "number"), ("地区", 1, "text"), ("利润", 2, "number")],
        cat_index=1, target_indexes=[0, 2],
    )
    assert spec.series_ranges == ["A1:A4", "C1:C4"]
    assert spec.categories_range == "B2:B4"
    # 兼容矩形仍填充（旧消费方不受影响）
    assert spec.data_range == "B1:C4"
    assert spec.combo_types == ["column", "line"]
    assert spec.combo_secondary == [False, True]


# ---------------------------------------------------------------
# 场景 1+2：类别列在中间 / 右侧
# ---------------------------------------------------------------

def test_category_column_in_middle():
    spec = _build(
        [("销量", 0, "number"), ("地区", 1, "text"), ("利润", 2, "number")],
        cat_index=1, target_indexes=[0, 2],
    )
    chart = _render(spec)
    assert _series_value_refs(chart) == ["'Sheet1'!$A$2:$A$4", "'Sheet1'!$C$2:$C$4"]
    assert _series_title_refs(chart) == ["'Sheet1'!A1", "'Sheet1'!C1"]
    assert _category_ref(chart) == "'Sheet1'!$B$2:$B$4"


def test_category_column_on_right():
    spec = _build(
        [("销量", 0, "number"), ("利润", 1, "number"), ("地区", 2, "text")],
        cat_index=2, target_indexes=[0, 1],
    )
    chart = _render(spec)
    assert _series_value_refs(chart) == ["'Sheet1'!$A$2:$A$4", "'Sheet1'!$B$2:$B$4"]
    assert _series_title_refs(chart) == ["'Sheet1'!A1", "'Sheet1'!B1"]
    assert _category_ref(chart) == "'Sheet1'!$C$2:$C$4"


# ---------------------------------------------------------------
# 场景 3+4：非连续数据列（B、D、G），以及与中间类别列叠加
# ---------------------------------------------------------------

def test_non_contiguous_series_columns():
    columns = [
        ("地区", 0, "text"), ("Q1", 1, "number"), ("备注", 2, "text"),
        ("Q2", 3, "number"), ("其他", 4, "text"), ("其他2", 5, "text"),
        ("Q3", 6, "number"),
    ]
    spec = _build(columns, cat_index=0, target_indexes=[1, 3])
    chart = _render(spec)
    # 系列引用必须是 B 与 D 本身，不得吞入中间的 C 列
    assert _series_value_refs(chart) == ["'Sheet1'!$B$2:$B$4", "'Sheet1'!$D$2:$D$4"]
    assert _series_title_refs(chart) == ["'Sheet1'!B1", "'Sheet1'!D1"]
    assert _category_ref(chart) == "'Sheet1'!$A$2:$A$4"


def test_category_middle_and_non_contiguous_series():
    columns = [
        ("Q1", 0, "number"), ("地区", 1, "text"), ("备注", 2, "text"),
        ("Q2", 3, "number"), ("Q3", 4, "number"),
    ]
    spec = _build(columns, cat_index=1, target_indexes=[0, 3])
    chart = _render(spec)
    assert _series_value_refs(chart) == ["'Sheet1'!$A$2:$A$4", "'Sheet1'!$D$2:$D$4"]
    assert _series_title_refs(chart) == ["'Sheet1'!A1", "'Sheet1'!D1"]
    assert _category_ref(chart) == "'Sheet1'!$B$2:$B$4"


# ---------------------------------------------------------------
# 场景 5：连续旧场景无回归（显式路径与旧矩形拆分产出相同引用）
# ---------------------------------------------------------------

def test_contiguous_legacy_layout_matches_old_semantics():
    columns = [("地区", 0, "text"), ("销量", 1, "number"), ("利润", 2, "number")]
    spec = _build(columns, cat_index=0, target_indexes=[1, 2])
    chart = _render(spec)
    assert _series_value_refs(chart) == ["'Sheet1'!$B$2:$B$4", "'Sheet1'!$C$2:$C$4"]
    assert _series_title_refs(chart) == ["'Sheet1'!B1", "'Sheet1'!C1"]
    assert _category_ref(chart) == "'Sheet1'!$A$2:$A$4"


def test_legacy_spec_without_series_ranges_uses_rectangle():
    """无 series_ranges 的旧 ChartSpec：矩形拆分路径行为不变。"""
    spec = ChartSpec(
        chart_type="combo",
        title="旧式",
        data_range="A1:C4",
        categories_range="A2:A4",
        combo_types=["column", "line"],
        combo_secondary=[False, True],
    )
    chart = _render(spec)
    assert _series_value_refs(chart) == ["'Sheet1'!$B$2:$B$4", "'Sheet1'!$C$2:$C$4"]
    assert _category_ref(chart) == "'Sheet1'!$A$2:$A$4"


# ---------------------------------------------------------------
# 场景 6：combo 语义保持（柱+线组合、次轴配方、titles_from_data）
# ---------------------------------------------------------------

def test_combo_semantics_preserved():
    spec = _build(
        [("销量", 0, "number"), ("地区", 1, "text"), ("利润", 2, "number")],
        cat_index=1, target_indexes=[0, 2],
    )
    chart = _render(spec)
    sub_types = [type(c).__name__ for c in _sub_charts(chart)]
    assert sub_types == ["BarChart", "LineChart"]
    # 次轴配方保持：主图 y 轴 crosses=max，折线图挂 200 号次轴
    assert chart.y_axis.crosses == "max"
    line_chart = _sub_charts(chart)[1]
    assert line_chart.y_axis.axId == 200
    assert chart.type == "col"
    # 系列标题均取自各自表头单元格
    assert all(ref is not None for ref in _series_title_refs(chart))


# ---------------------------------------------------------------
# 场景 7：无类别列（纯数值表）请求 combo 不崩溃，降级为柱状图
# ---------------------------------------------------------------

def test_combo_without_category_column_degrades_to_column():
    """纯数值表没有文本/日期类别列，_find_category_column 返回 None。

    此前 combo 分支把 None 直接传给 _build_combo_spec，内部访问
    cat_col.index 会触发 AttributeError；修复后降级为普通柱状图。
    """
    gen = ChartGenerator(profile=_profile(
        [("Q1", 0, "number"), ("Q2", 1, "number")],
        row_count=3,
    ))
    specs = gen.generate_from_text("组合图", chart_type="combo")
    # 不崩溃且产出降级结果：至少一个图表、且无 combo 类型
    assert specs, "无类别列时应降级产出普通柱状图"
    assert all(spec.chart_type != "combo" for spec in specs)

