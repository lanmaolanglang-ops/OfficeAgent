"""models.col_letter 统一列字母换算的针对性测试。

根因背景：chart_generator._col_letter 按 1-based 实现（调用方全部
`index + 1`），formula_generator._col_letter 按 0-based 实现，同名
方法跨类口径漂移。现统一为 models.col_letter（0-based，对齐
ColumnInfo.index 约定），两个生成器均委托之。
"""
import pytest
from openpyxl.utils import get_column_letter

from office_agent.excel_agent.models import col_letter


class TestColLetter:
    @pytest.mark.parametrize("index", [0, 1, 25, 26, 27, 51, 52, 701, 702, 16383])
    def test_matches_openpyxl(self, index):
        assert col_letter(index) == get_column_letter(index + 1)

    def test_boundary_letters(self):
        assert col_letter(0) == "A"
        assert col_letter(25) == "Z"
        assert col_letter(26) == "AA"
        assert col_letter(16383) == "XFD"

    @pytest.mark.parametrize("bad", [-1, "A", 1.5, None])
    def test_invalid_index_raises(self, bad):
        with pytest.raises(ValueError):
            col_letter(bad)


class TestGeneratorDelegation:
    """两个生成器的同名方法必须口径一致（0-based）。"""

    def test_chart_generator_delegates_zero_based(self):
        from office_agent.excel_agent.chart_generator import ChartGenerator
        assert ChartGenerator._col_letter(0) == "A"
        assert ChartGenerator._col_letter(2) == "C"

    def test_formula_generator_delegates_zero_based(self):
        from office_agent.excel_agent.formula_generator import FormulaGenerator
        assert FormulaGenerator._col_letter(0) == "A"
        assert FormulaGenerator._col_letter(2) == "C"

    def test_both_generators_agree(self):
        from office_agent.excel_agent.chart_generator import ChartGenerator
        from office_agent.excel_agent.formula_generator import FormulaGenerator
        for i in (0, 7, 25, 26, 100):
            assert ChartGenerator._col_letter(i) == FormulaGenerator._col_letter(i) == col_letter(i)

    def test_chart_generator_source_has_no_manual_plus_one(self):
        """chart_generator 调用方不得再手动 +1（口径在统一入口内）。"""
        import inspect
        from office_agent.excel_agent import chart_generator
        src = inspect.getsource(chart_generator)
        assert "_col_letter(cat_col.index + 1)" not in src
        assert "_col_letter(col.index + 1)" not in src
