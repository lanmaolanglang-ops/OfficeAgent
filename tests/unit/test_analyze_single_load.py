"""analyze_file 单次加载专项测试（清单：analyze_file 对同一文件重复加载）。

全簿分析时 openpyxl 阶段已把全部数据载入内存，schema 分析必须复用
该数据（analyze_schema_frames），不得再经 pandas/openpyxl 二次解析文件。
"""
import openpyxl
import pandas as pd

from office_agent.excel_agent.analysis_engine import AnalysisEngine
from office_agent.excel_agent.data_analyzer import DataAnalyzer


def _make_workbook(path):
    wb = openpyxl.Workbook()
    for index, name in enumerate(("甲", "乙")):
        ws = wb.active if index == 0 else wb.create_sheet()
        ws.title = name
        ws.append(["名称", "金额"])
        ws.append(["A", 1])
        ws.append(["B", 2])
    wb.save(path)


class TestSingleLoad:
    def test_full_analysis_does_not_reparse_file(self, tmp_path, monkeypatch):
        src = tmp_path / "data.xlsx"
        _make_workbook(src)

        def _forbidden(*args, **kwargs):
            raise AssertionError("analyze_file 全簿分析不得二次解析文件")

        # 文件级入口两条路径（pandas 主路径 + openpyxl 降级）全部封死：
        # 若代码回退到 analyze_schema(file_path)，schema 必为 None
        monkeypatch.setattr(pd, "ExcelFile", _forbidden)
        monkeypatch.setattr(DataAnalyzer, "_analyze_with_openpyxl", _forbidden)

        engine = AnalysisEngine()
        report = engine.analyze_file(str(src))

        assert report.sheet_name == "全部工作表"
        assert engine.schema is not None
        assert engine.schema.total_sheets == 2
        assert engine.schema.total_rows == 4
        assert {s.name for s in engine.schema.sheets} == {"甲", "乙"}

    def test_single_sheet_keeps_file_level_schema(self, tmp_path, monkeypatch):
        """指定单表时 schema 语义覆盖全簿，保留文件级 analyze_schema。"""
        src = tmp_path / "data.xlsx"
        _make_workbook(src)
        calls = []
        original = DataAnalyzer.analyze_schema

        def _spy(self, file_path):
            calls.append(file_path)
            return original(self, file_path)

        monkeypatch.setattr(DataAnalyzer, "analyze_schema", _spy)
        engine = AnalysisEngine()
        report = engine.analyze_file(str(src), sheet_name="甲")

        assert report.sheet_name == "甲"
        assert calls == [str(src)]
        assert engine.schema.total_sheets == 2

    def test_schema_frames_matches_file_based_result(self, tmp_path):
        """内存帧入口与文件级入口对同一工作簿产出等价 schema。"""
        src = tmp_path / "data.xlsx"
        _make_workbook(src)
        frames = {
            name: pd.DataFrame([["A", 1], ["B", 2]], columns=["名称", "金额"])
            for name in ("甲", "乙")
        }
        analyzer = DataAnalyzer()
        from_frames = analyzer.analyze_schema_frames(str(src), frames)
        from_file = analyzer.analyze_schema(str(src))

        assert from_frames.total_sheets == from_file.total_sheets == 2
        assert from_frames.total_rows == from_file.total_rows == 4
        assert [s.name for s in from_frames.sheets] == \
               [s.name for s in from_file.sheets]
