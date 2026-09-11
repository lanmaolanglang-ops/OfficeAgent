"""data_analyzer 列名健壮性回归测试（P1-11）。

重点不是"不抛异常"，而是**断言真实分析结果**：
每列都被独立分析、列名保留身份、语义/单位推断不受脏列名影响。
"""
import pandas as pd
import pytest

from office_agent.excel_agent.data_analyzer import (
    DataAnalyzer,
    _is_missing_label,
    _label_to_text,
    _match_key,
    _unique_display_name,
)


@pytest.fixture
def analyzer():
    return DataAnalyzer()


def _schema(analyzer, columns, rows=None, ncol=None):
    ncol = ncol if ncol is not None else len(columns)
    if rows is None:
        rows = [[1] * ncol] * 3
    return analyzer._analyze_sheet_schema(pd.DataFrame(rows, columns=columns), "S")


# ------------------------------------------------------------------
# 1. 正常 / 中文列名
# ------------------------------------------------------------------

def test_normal_string_columns(analyzer):
    df = pd.DataFrame({
        "订单号": ["A1", "A2", "A3"],
        "销售额": [1000.0, 2000.0, 3000.0],
        "数量": [1, 2, 3],
    })
    sheet = analyzer._analyze_sheet_schema(df, "S")

    assert [c.name for c in sheet.columns] == ["订单号", "销售额", "数量"]
    assert [c.index for c in sheet.columns] == [0, 1, 2]
    assert sheet.columns[1].data_type == "number"
    assert sheet.columns[1].semantic_type == "amount"
    assert sheet.columns[1].unit == "元"


def test_chinese_columns_keep_raw_names(analyzer):
    sheet = _schema(analyzer, ["销售额", "数量", "日期"])
    assert [c.name for c in sheet.columns] == ["销售额", "数量", "日期"]


# ------------------------------------------------------------------
# 2. 非字符串列名
# ------------------------------------------------------------------

def test_int_columns(analyzer):
    sheet = _schema(analyzer, [1, 2, 3])
    assert [c.name for c in sheet.columns] == ["1", "2", "3"]
    assert all(c.data_type == "number" for c in sheet.columns)


def test_float_and_bool_columns(analyzer):
    sheet = _schema(analyzer, ["A", 1, 3.5, True], ncol=4)
    assert [c.name for c in sheet.columns] == ["A", "1", "3.5", "True"]


def test_none_and_empty_column_names_fall_back(analyzer):
    """None / NaN / 空串不能变成 'None' / 'nan' / '' 这种误导性名字。"""
    sheet = _schema(analyzer, [None, "", "   ", "B"], ncol=4)
    names = [c.name for c in sheet.columns]
    assert names[0] == "列1"
    assert names[1] == "列2"
    assert names[2] == "列3"
    assert names[3] == "B"
    assert len(set(names)) == 4, "回退名不得与真实列名冲突"


# ------------------------------------------------------------------
# 3. 重复列名：核心缺陷
# ------------------------------------------------------------------

def test_duplicate_columns_do_not_crash(analyzer):
    """旧实现 df[col_name] 在重名时返回 DataFrame，int(Series) 直接 TypeError。"""
    sheet = _schema(analyzer, ["销售额", "销售额", "数量"], ncol=3)
    assert len(sheet.columns) == 3
    assert all(isinstance(c.data_type, str) for c in sheet.columns)


def test_duplicate_columns_keep_distinct_identity(analyzer):
    sheet = _schema(analyzer, ["销售额", "销售额", "数量"], ncol=3)
    names = [c.name for c in sheet.columns]
    assert len(set(names)) == 3
    assert names[0] == "销售额"
    assert names[1] == "销售额 (2)"
    assert names[2] == "数量"


def test_duplicate_columns_are_analyzed_independently(analyzer):
    """两列同名但内容不同：必须各按自己的数据分析，不能串列。"""
    df = pd.DataFrame(
        [[1000.0, "甲"], [2000.0, "乙"], [3000.0, "丙"]],
        columns=["字段", "字段"],
    )
    sheet = analyzer._analyze_sheet_schema(df, "S")

    first, second = sheet.columns[0], sheet.columns[1]
    assert first.data_type == "number", "第一列应按数值分析"
    assert second.data_type == "text", "第二列应按文本分析，不能沿用第一列结果"
    assert first.sum_value == pytest.approx(6000.0)
    assert second.sample_values == ["甲", "乙", "丙"]


def test_int_and_str_label_collision(analyzer):
    """int 1 与 str "1" 是两个不同的列，str() 后不能塌缩成同一身份。"""
    sheet = _schema(analyzer, [1, "1", "2"], ncol=3)
    names = [c.name for c in sheet.columns]
    assert names[0] == "1"
    assert names[1] == "1 (2)"
    assert len(set(names)) == 3


# ------------------------------------------------------------------
# 4. 空白 / 特殊符号 / MultiIndex
# ------------------------------------------------------------------

def test_whitespace_column_name_keeps_raw_but_still_matches(analyzer):
    sheet = _schema(analyzer, ["  销售额  ", "B"], ncol=2)
    assert sheet.columns[0].name == "  销售额  ", "展示名保留用户原始写法"
    assert sheet.columns[0].semantic_type == "amount", "匹配应忽略前后空格"
    assert sheet.columns[0].unit == "元"


def test_special_character_columns(analyzer):
    sheet = _schema(analyzer, ["金额(元)", "A/B", "100%"], ncol=3)
    assert [c.name for c in sheet.columns] == ["金额(元)", "A/B", "100%"]
    assert sheet.columns[0].semantic_type == "amount"


def test_mixed_type_columns(analyzer):
    sheet = _schema(analyzer, ["A", 1, 3.5, None, True], ncol=5)
    names = [c.name for c in sheet.columns]
    assert len(set(names)) == 5
    assert "None" not in names
    assert "nan" not in names


def test_multiindex_columns(analyzer):
    """MultiIndex 列名应拼接层级，而不是变成元组 repr。"""
    columns = pd.MultiIndex.from_tuples([("销售额", "元"), ("数量", "个")])
    df = pd.DataFrame([[1, 2], [3, 4], [5, 6]], columns=columns)
    sheet = analyzer._analyze_sheet_schema(df, "M")

    names = [c.name for c in sheet.columns]
    assert names == ["销售额 / 元", "数量 / 个"]
    assert all("(" not in n and "'" not in n for n in names)


# ------------------------------------------------------------------
# 5. 辅助函数
# ------------------------------------------------------------------

def test_is_missing_label():
    assert _is_missing_label(None) is True
    assert _is_missing_label(float("nan")) is True
    assert _is_missing_label(pd.NA) is True
    assert _is_missing_label("") is True
    assert _is_missing_label("   ") is True
    assert _is_missing_label("销售额") is False
    assert _is_missing_label(0) is False
    assert _is_missing_label(False) is False


def test_label_to_text():
    assert _label_to_text("销售额") == "销售额"
    assert _label_to_text(1) == "1"
    assert _label_to_text(None) == ""
    assert _label_to_text(("销售额", "元")) == "销售额 / 元"


def test_match_key_is_normalized_only():
    assert _match_key("  销售额  ") == "销售额"
    assert _match_key("Amount") == "amount"
    assert _match_key(1) == "1"


def test_unique_display_name_does_not_mutate_raw():
    used = {}
    assert _unique_display_name("销售额", 0, used) == "销售额"
    assert _unique_display_name("销售额", 1, used) == "销售额 (2)"
    assert _unique_display_name(None, 5, used) == "列6"


# ------------------------------------------------------------------
# 6. 既有正常分析不回归（SheetInfo 旧接口同样受益）
# ------------------------------------------------------------------

def test_legacy_sheet_info_path_handles_duplicates(analyzer):
    df = pd.DataFrame([[1, "x"], [2, "y"]], columns=["A", "A"])
    info = analyzer._analyze_sheet_to_info(df, "S")
    assert [c.name for c in info.columns] == ["A", "A (2)"]


def test_profile_shape_and_stats_unchanged(analyzer):
    df = pd.DataFrame({
        "日期": ["2024-01-01", "2024-02-01", "2024-03-01"],
        "销售额": [100.0, 200.0, 300.0],
        "数量": [1, 2, 3],
    })
    sheet = analyzer._analyze_sheet_schema(df, "S")

    assert sheet.row_count == 3
    assert sheet.col_count == 3
    assert sheet.columns[1].min_value == pytest.approx(100.0)
    assert sheet.columns[1].max_value == pytest.approx(300.0)
    assert sheet.columns[1].sum_value == pytest.approx(600.0)
    assert sheet.columns[2].semantic_type == "quantity"
    assert sheet.columns[2].unit == "个"
