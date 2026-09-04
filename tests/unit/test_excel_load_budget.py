"""Excel 编排加载预算钉住测试（清单 183 复核关闭）。

实证结论（2026-09-04）：process_file 全程对同一文件的整本解析为
- 输入文件 2 次：pandas 画像（pd.ExcelFile，内部 openpyxl 只读 values 视图）
  + openpyxl data_only=False（编辑视图）；
- 输出文件 2 次：结构检查（data_only=False）+ 缓存错误值扫描
  （data_only=True, read_only）。

openpyxl 无法在单次加载中同时提供公式视图与缓存值视图，pandas 画像
也必须基于值视图（否则公式列被误判为文本），因此这 4 次解析是
**引擎视图固有需要**，不存在可消除的冗余；合并需要自研双视图解析器，
属明确禁止的大重构。本测试钉住加载预算：任何回到"第三次输入加载"
（如脆弱元素检测曾单独整本解析，已修复）的回归都会立即失败。
"""
import os

import openpyxl
import pandas as pd
import pytest

import office_agent.excel_agent.data_analyzer as data_analyzer
import office_agent.excel_agent.excel_service as excel_service
import office_agent.excel_agent.quality_checker as quality_checker
from office_agent.excel_agent.excel_orchestrator import ExcelOrchestrator


@pytest.fixture
def load_spy(monkeypatch):
    """记录各模块对 load_workbook / pd.ExcelFile 的调用。"""
    calls = []
    real_lw = openpyxl.load_workbook

    def spy_lw(file, *args, **kwargs):
        calls.append({
            "path": str(file),
            "data_only": kwargs.get("data_only"),
            "read_only": kwargs.get("read_only"),
        })
        return real_lw(file, *args, **kwargs)

    real_ef = pd.ExcelFile

    def spy_ef(file, *args, **kwargs):
        calls.append({"path": str(file), "pandas": True})
        return real_ef(file, *args, **kwargs)

    monkeypatch.setattr(excel_service, "load_workbook", spy_lw)
    monkeypatch.setattr(quality_checker, "load_workbook", spy_lw)
    monkeypatch.setattr(data_analyzer.pd, "ExcelFile", spy_ef)
    # excel_orchestrator._detect_fragile_elements 局部 import 走 openpyxl 命名空间
    monkeypatch.setattr(openpyxl, "load_workbook", spy_lw)
    return calls


def _make_input(tmp_path):
    src = tmp_path / "in.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["月份", "销售额", "成本"])
    for i in range(1, 13):
        ws.append([i, i * 100, i * 60])
    wb.save(src)
    return src


class TestOrchestrationLoadBudget:
    def test_input_file_loaded_exactly_twice(self, tmp_path, load_spy):
        src = _make_input(tmp_path)
        out = tmp_path / "out.xlsx"
        result = ExcelOrchestrator().process_file(str(src), "添加图表", output_path=str(out))
        assert result.success

        input_loads = [c for c in load_spy if os.path.basename(c["path"]).startswith("in.xlsx")]
        # pandas 画像一次（含其内部 values 视图加载）+ 编辑视图一次，无第三次
        pandas_loads = [c for c in input_loads if c.get("pandas")]
        edit_loads = [c for c in input_loads
                      if not c.get("pandas") and c["data_only"] is False]
        assert len(pandas_loads) == 1, f"输入 pandas 画像应恰好 1 次: {input_loads}"
        assert len(edit_loads) == 1, f"输入编辑视图应恰好 1 次: {input_loads}"

    def test_no_extra_openpyxl_parse_of_input(self, tmp_path, load_spy):
        """脆弱元素检测等辅助腿不得再对输入文件单独整本解析（清单 183 防回退）。"""
        src = _make_input(tmp_path)
        out = tmp_path / "out.xlsx"
        ExcelOrchestrator().process_file(str(src), "", output_path=str(out))

        # 我们模块对输入文件的直接 load_workbook（不含 pandas 内部那次）：
        # 只允许 excel_service.open 的编辑视图一次
        direct_input_loads = [
            c for c in load_spy
            if c["path"].endswith("in.xlsx") and not c.get("pandas")
        ]
        assert len(direct_input_loads) == 1
        assert direct_input_loads[0]["data_only"] is False

    def test_output_quality_check_two_views_only(self, tmp_path, load_spy):
        """输出文件质检恰好两次：结构视图 + 缓存值视图，不得再多。"""
        src = _make_input(tmp_path)
        out = tmp_path / "out.xlsx"
        ExcelOrchestrator().process_file(str(src), "", output_path=str(out))

        output_loads = [
            c for c in load_spy
            if c["path"].endswith("out.xlsx") and not c.get("pandas")
        ]
        views = sorted((c["data_only"], bool(c["read_only"])) for c in output_loads)
        assert views == [(False, False), (True, True)], (
            f"输出质检应为结构+缓存值两个视图各一次: {output_loads}"
        )

    def test_total_parse_count_is_four(self, tmp_path, load_spy):
        """总预算：输入 2 + 输出 2 = 4 次整本解析（不含 pandas 内部那一次）。"""
        src = _make_input(tmp_path)
        out = tmp_path / "out.xlsx"
        ExcelOrchestrator().process_file(str(src), "", output_path=str(out))

        module_loads = [c for c in load_spy if not c.get("pandas")]
        # 含 pandas 内部触发的 openpyxl 加载时总数为 5；我们只钉模块级预算
        assert len(module_loads) == 4, f"整本解析预算应为 4: {load_spy}"
