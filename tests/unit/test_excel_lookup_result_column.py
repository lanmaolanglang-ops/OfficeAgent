"""P1-10 follow-up 回归测试：lookup 结果列不得空出一列。

背景：
    上一轮修好了 table_array / col_index_num，但独立验收发现结果列仍为
    ``_col_letter(sheet.col_count + 1)``。

语义约定（已由源码确认，非假设）：
    * ``col_letter`` 是 **0-based**（0→A、25→Z、26→AA，见 models.col_letter 注释）。
    * ``SheetInfo.col_count`` 是 **数量**（``len(df.columns)`` / ``ws.max_column``）。
    * 因此 6 列数据的最后一列索引是 5(F)，下一个空列索引是 6 → **G**。
    * 同文件 ``generate_from_text`` 里 ``next_result_col = sheet.col_count``
      是框架自身的"下一个空列"约定，逐行公式直接用它转字母、不再 +1。

缺陷：
    ``_generate_lookup``、``generate_vlookup_formulas``、
    ``generate_xlookup_formulas`` 三处用了 ``col_count + 1``，
    导致 6 列数据把结果写到 H，中间空出 G。
"""
import pytest

from office_agent.excel_agent.formula_generator import (
    FORMULA_TEMPLATES,
    FormulaGenerator,
)
from office_agent.excel_agent.models import ColumnInfo, SheetInfo, col_letter

VLOOKUP_TPL = FORMULA_TEMPLATES["vlookup"]
XLOOKUP_TPL = FORMULA_TEMPLATES["xlookup"]
INDEX_MATCH_TPL = FORMULA_TEMPLATES["index_match"]


def _col_index(letters: str) -> int:
    """Excel 列字母 -> 0-based 索引（col_letter 的逆运算，仅测试使用）。"""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


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


def _targets(formulas):
    return [f.target_cell for f in formulas]


# ------------------------------------------------------- 1. 具体列字母断言


def test_six_columns_result_column_is_g():
    """6 列数据（A:F）-> 结果列必须是紧邻的 G，不能是 H。"""
    formulas = _gen(0, 1, col_count=6)
    assert _targets(formulas) == ["G2", "G3", "G4"]


def test_twentysix_columns_result_column_is_aa():
    """26 列数据（A:Z）-> 结果列 AA。"""
    formulas = _gen(0, 1, col_count=26)
    assert _targets(formulas) == ["AA2", "AA3", "AA4"]


def test_twentyseven_columns_result_column_is_ab():
    """27 列数据（A:AA）-> 结果列 AB。"""
    formulas = _gen(0, 1, col_count=27)
    assert _targets(formulas) == ["AB2", "AB3", "AB4"]


# ------------------------------------------------- 2. 不得空列（结构化断言）


@pytest.mark.parametrize("col_count", [2, 6, 25, 26, 27, 52, 53])
def test_result_column_is_immediately_after_last_data_column(col_count):
    """结果列索引必须 == 数据区末列索引 + 1（中间不得空列）。"""
    formulas = _gen(0, 1, col_count=col_count)
    assert formulas, "未生成公式"

    last_data_idx = col_count - 1
    for target in _targets(formulas):
        letters = "".join(c for c in target if c.isalpha())
        assert _col_index(letters) == last_data_idx + 1
        # 与项目唯一换算入口保持一致
        assert letters == col_letter(col_count)


def test_no_gap_between_last_data_column_and_result_column():
    """6 列数据时 F 与结果列之间必须相邻，语义化断言。"""
    formulas = _gen(0, 1, col_count=6)
    letters = "".join(c for c in formulas[0].target_cell if c.isalpha())
    assert _col_index(letters) - _col_index("F") == 1


# ------------------------------------------- 3. 三种模板走同一结果列规则


@pytest.mark.parametrize(
    "tpl", [VLOOKUP_TPL, XLOOKUP_TPL, INDEX_MATCH_TPL],
    ids=["vlookup", "xlookup", "index_match"],
)
def test_all_lookup_templates_use_adjacent_result_column(tpl):
    """VLOOKUP / XLOOKUP / INDEX-MATCH 结果列规则必须一致。"""
    formulas = _gen(0, 1, col_count=6, tpl=tpl)
    assert _targets(formulas) == ["G2", "G3", "G4"]


def test_reverse_lookup_fallback_keeps_adjacent_column():
    """返回列在查找列左侧 -> 回退 XLOOKUP，结果列同样不得偏移。"""
    formulas = _gen(5, 1, col_count=6)  # lookup=F, return=B
    assert formulas, "回退路径应生成公式"
    assert all(f.category == "xlookup" for f in formulas)
    assert _targets(formulas) == ["G2", "G3", "G4"]


# ------------------------------------------- 4. 公开 API 与内部路径一致


def test_generate_vlookup_formulas_result_column():
    """公开 generate_vlookup_formulas 与内部路径使用同一结果列。"""
    gen = FormulaGenerator()
    formulas = gen.generate_vlookup_formulas(0, 1, _sheet(col_count=6))
    assert _targets(formulas) == ["G2", "G3", "G4"]


def test_generate_xlookup_formulas_result_column():
    """公开 generate_xlookup_formulas 结果列紧跟数据区。"""
    gen = FormulaGenerator()
    formulas = gen.generate_xlookup_formulas(0, 1, _sheet(col_count=6))
    assert _targets(formulas) == ["G2", "G3", "G4"]


def test_vlookup_fallback_to_xlookup_result_column():
    """VLOOKUP 反向查找回退后，结果列仍为 G。"""
    gen = FormulaGenerator()
    formulas = gen.generate_vlookup_formulas(5, 1, _sheet(col_count=6))
    assert _targets(formulas) == ["G2", "G3", "G4"]


# ------------------------------------------- 5. 与逐行公式口径一致


def test_row_wise_and_lookup_agree_on_first_free_column():
    """逐行公式（以 col_count 为起点）与 lookup 必须落在同一列。"""
    gen = FormulaGenerator()
    sheet = _sheet(col_count=6)
    # 逐行公式需要一个数值型目标列，否则内部会退回空结果
    num_col = _col("num", 0)
    num_col.data_type = "number"
    row_wise = gen._generate_row_wise(
        FORMULA_TEMPLATES["rank"], [num_col], sheet, 2, "",
        start_result_col=sheet.col_count,
    )
    lookup = _gen(0, 1, col_count=6)

    assert row_wise, "逐行公式未生成，测试前置条件不成立"
    row_wise_col = "".join(c for c in row_wise[0].target_cell if c.isalpha())
    lookup_col = "".join(c for c in lookup[0].target_cell if c.isalpha())
    assert row_wise_col == "G"
    assert lookup_col == row_wise_col


# ------------------------------------------- 6. 公式正文与目标列自洽


def test_formula_body_row_matches_target_row():
    """写在 G{row} 的公式，其正文引用的查找单元格必须同为 {row}。"""
    formulas = _gen(2, 5, col_count=6, row_count=3)  # lookup=C
    assert [f.target_cell for f in formulas] == ["G2", "G3", "G4"]
    for f in formulas:
        row = "".join(c for c in f.target_cell if c.isdigit())
        # 正文引用查找列同一行（C2/C3/C4），而不是行号错位
        assert f"C{row}" in f.formula


def _col_letters_of(cell: str) -> str:
    return "".join(c for c in cell if c.isalpha())
