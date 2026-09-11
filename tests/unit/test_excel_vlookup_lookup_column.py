"""P1-10 回归测试：VLOOKUP 的 table_array 必须从真正的查找列起。

历史缺陷：
    table_range = f"A:{末列}"，col_index_num = return_col.index + 1
当查找列不是 A 时，VLOOKUP 会在 A 列里找本应属于 C 列的查找值，恒为 #N/A，
再被外层 IFERROR 吞成空字符串——表面成功、结果全空。

正确语义：
    table_array 首列 = 查找列；col_index_num = return_idx - lookup_idx + 1
返回列位于查找列左侧时 VLOOKUP 结构上无法表达，回退 XLOOKUP。
"""
import pytest

from office_agent.excel_agent.formula_generator import (
    FORMULA_TEMPLATES,
    FormulaGenerator,
)
from office_agent.excel_agent.models import ColumnInfo, SheetInfo

VLOOKUP_TPL = FORMULA_TEMPLATES["vlookup"]
XLOOKUP_TPL = FORMULA_TEMPLATES["xlookup"]
INDEX_MATCH_TPL = FORMULA_TEMPLATES["index_match"]


def _sheet(col_count=6, row_count=3):
    columns = [ColumnInfo(name=f"col{i}", index=i) for i in range(col_count)]
    return SheetInfo(name="Sheet1", row_count=row_count,
                     col_count=col_count, columns=columns)


def _col(name, index):
    return ColumnInfo(name=name, index=index)


def _gen(lookup_idx, return_idx, col_count=6, row_count=3, tpl=VLOOKUP_TPL):
    gen = FormulaGenerator()
    return gen._generate_lookup(
        tpl,
        {"lookup_col": _col("查找列", lookup_idx),
         "return_col": _col("返回列", return_idx)},
        [],
        _sheet(col_count, row_count),
        data_start_row=2,
    )


def _first(formulas):
    assert formulas, "未生成任何公式"
    return formulas[0].formula


# ---------------------------------------------------------------- 核心用例


def test_vlookup_lookup_c_return_f():
    """lookup=C, return=F -> VLOOKUP(C2,C:F,4,FALSE)。"""
    formula = _first(_gen(2, 5))
    assert formula == '=IFERROR(VLOOKUP(C2,C:F,4,FALSE),"")'


def test_vlookup_lookup_aa_return_ad():
    """lookup=AA, return=AD -> 多位列字母同样按相对偏移。"""
    formula = _first(_gen(26, 29, col_count=30))
    assert formula == '=IFERROR(VLOOKUP(AA2,AA:AD,4,FALSE),"")'


def test_vlookup_lookup_a_keeps_legacy_shape():
    """lookup=A 时结果必须与历史一致（表范围仍到数据区末列，不得回归）。"""
    formula = _first(_gen(0, 1))
    assert formula == '=IFERROR(VLOOKUP(A2,A:F,2,FALSE),"")'


def test_vlookup_return_adjacent_to_lookup():
    """返回列紧邻查找列 -> col_index_num == 2。"""
    formula = _first(_gen(3, 4))
    assert formula == '=IFERROR(VLOOKUP(D2,D:F,2,FALSE),"")'


def test_vlookup_return_left_of_lookup_falls_back_to_xlookup():
    """返回列在查找列左侧：不得生成必然 #N/A 的伪 VLOOKUP。"""
    formulas = _gen(5, 2)
    formula = _first(formulas)
    assert "VLOOKUP" not in formula
    assert "XLOOKUP" in formula
    # 查找列 F、返回列 C，语义正确（不是被 IFERROR 掩盖的空壳）
    assert formula == '=IFERROR(XLOOKUP(F2,F2:F4,C2:C4),"")'


def test_vlookup_range_helper_returns_relative_index():
    gen = FormulaGenerator()
    # (lookup, return, last) -> (table_range, col_index_num)
    assert gen._build_vlookup_range(2, 5, 5) == ("C:F", 4)
    assert gen._build_vlookup_range(0, 1, 5) == ("A:F", 2)
    assert gen._build_vlookup_range(26, 29, 29) == ("AA:AD", 4)
    # 返回列在查找列左侧 -> 无法用 VLOOKUP 表达
    assert gen._build_vlookup_range(5, 2, 5) is None


@pytest.mark.parametrize("lookup_idx,return_idx", [
    (0, 1), (0, 5), (1, 3), (2, 5), (3, 4), (26, 29), (26, 27),
])
def test_vlookup_index_equals_relative_offset(lookup_idx, return_idx):
    """跨多组列组合：col_index_num 恒等于 return - lookup + 1，且范围首列=查找列。"""
    from office_agent.excel_agent.models import col_letter

    gen = FormulaGenerator()
    table_range, col_index_num = gen._build_vlookup_range(
        lookup_idx, return_idx, max(return_idx, lookup_idx)
    )
    assert col_index_num == return_idx - lookup_idx + 1
    assert table_range.startswith(f"{col_letter(lookup_idx)}:")
    # table_array 的第 col_index_num 列必须正好是返回列
    start = lookup_idx
    assert start + col_index_num - 1 == return_idx


# ------------------------------------------------- 公开 API 与相邻模板


def test_generate_vlookup_formulas_uses_real_lookup_column():
    gen = FormulaGenerator()
    formulas = gen.generate_vlookup_formulas(2, 5, _sheet(6, 3))
    assert formulas[0].formula == '=IFERROR(VLOOKUP(C2,C:F,4,FALSE),"")'


def test_generate_vlookup_formulas_left_return_falls_back():
    gen = FormulaGenerator()
    formulas = gen.generate_vlookup_formulas(5, 2, _sheet(6, 3))
    assert "XLOOKUP" in formulas[0].formula


def test_xlookup_template_unaffected():
    """XLOOKUP 模板行为保持不变（不得被本次修改波及）。"""
    formula = _first(_gen(2, 5, tpl=XLOOKUP_TPL))
    assert formula == '=IFERROR(XLOOKUP(C2,C2:C4,F2:F4),"")'


def test_index_match_template_unaffected():
    formula = _first(_gen(2, 5, tpl=INDEX_MATCH_TPL))
    assert formula == '=IFERROR(INDEX(F2:F4,MATCH(C2,C2:C4,0)),"")'


def test_vlookup_writes_to_column_right_of_data():
    formulas = _gen(2, 5, col_count=6)
    assert formulas[0].target_cell == "H2"   # col_letter(col_count + 1) -> H
    assert [f.target_cell for f in formulas] == ["H2", "H3", "H4"]


def test_lookup_column_is_last_column_yields_no_formula():
    """查找列已是最后一列：没有可返回的数据列，不得生成越界公式。"""
    gen = FormulaGenerator()
    formulas = gen._generate_lookup(
        VLOOKUP_TPL,
        {"lookup_col": _col("末列", 5)},
        [],
        _sheet(6, 3),
        data_start_row=2,
    )
    assert formulas == []


def test_vlookup_result_column_is_the_return_column():
    """端到端语义断言：table_array 的第 N 列必须落在返回列上。

    这是对"公式字符串看起来合法但语义错误"的直接防回归：
    旧实现 A:F + index 6 虽然同样指向 F，但查找值取自 A 列，语义全错。
    """
    from office_agent.excel_agent.models import col_letter

    for lookup_idx in range(0, 5):
        for return_idx in range(lookup_idx + 1, 6):
            gen = FormulaGenerator()
            table_range, col_index_num = gen._build_vlookup_range(
                lookup_idx, return_idx, 5
            )
            start_letter, _ = table_range.split(":")
            assert start_letter == col_letter(lookup_idx)
            resolved = lookup_idx + col_index_num - 1
            assert col_letter(resolved) == col_letter(return_idx)
