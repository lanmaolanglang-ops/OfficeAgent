"""
Office Compatibility Tests - Office文件兼容性测试
测试 docx/pptx/xlsx 文件的读写和格式兼容性
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.qa_framework import (
    TestCategory, TestSeverity, QATestRunner,
    create_test_word, create_test_excel, create_test_ppt,
)


def test_docx_read_write():
    """测试docx文件读写"""
    from docx import Document
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test.docx"
        create_test_word(path, "simple")
        assert path.exists()
        # 读取
        doc = Document(str(path))
        assert len(doc.paragraphs) > 0
        return True, f"docx读写正常, {len(doc.paragraphs)}段落"


def test_docx_complex_structure():
    """测试复杂docx结构（标题、表格、图片占位）"""
    from docx import Document
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "complex.docx"
        doc = Document()
        doc.add_heading("标题1", level=1)
        doc.add_paragraph("段落内容")
        doc.add_heading("标题2", level=2)
        table = doc.add_table(rows=3, cols=3)
        table.style = "Table Grid"
        for r in range(3):
            for c in range(3):
                table.cell(r, c).text = f"{r},{c}"
        doc.save(str(path))
        # 重新读取验证
        doc2 = Document(str(path))
        assert len(doc2.tables) == 1
        assert doc2.tables[0].cell(0, 0).text == "0,0"
        return True, "复杂docx结构正常"


def test_pptx_read_write():
    """测试pptx文件读写"""
    from pptx import Presentation
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test.pptx"
        create_test_ppt(path, slides=5)
        assert path.exists()
        prs = Presentation(str(path))
        assert len(prs.slides) == 5
        return True, f"pptx读写正常, {len(prs.slides)}页"


def test_pptx_layouts():
    """测试pptx多种布局"""
    from pptx import Presentation
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "layouts.pptx"
        prs = Presentation()
        layouts = prs.slide_layouts
        for i, layout in enumerate(layouts):
            slide = prs.slides.add_slide(layout)
            if slide.shapes.title:
                slide.shapes.title.text = f"布局{i}"
        prs.save(str(path))
        # 验证
        prs2 = Presentation(str(path))
        assert len(prs2.slides) == len(layouts)
        return True, f"pptx布局正常, {len(layouts)}种布局"


def test_xlsx_read_write():
    """测试xlsx文件读写"""
    from openpyxl import Workbook, load_workbook
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test.xlsx"
        create_test_excel(path, rows=100, cols=10)
        assert path.exists()
        wb = load_workbook(str(path))
        ws = wb.active
        assert ws.max_row == 101  # header + 100
        assert ws.max_column == 10
        return True, f"xlsx读写正常, {ws.max_row}行x{ws.max_column}列"


def test_xlsx_formulas():
    """测试xlsx公式"""
    from openpyxl import Workbook, load_workbook
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "formulas.xlsx"
        wb = Workbook()
        ws = wb.active
        ws["A1"] = 10
        ws["A2"] = 20
        ws["A3"] = "=SUM(A1:A2)"
        ws["B1"] = "=A1*2"
        wb.save(str(path))
        wb2 = load_workbook(str(path))
        ws2 = wb2.active
        assert ws2["A3"].value == "=SUM(A1:A2)"
        assert ws2["B1"].value == "=A1*2"
        return True, "xlsx公式正常"


def test_xlsx_charts():
    """测试xlsx图表"""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "charts.xlsx"
        wb = Workbook()
        ws = wb.active
        for i in range(1, 11):
            ws.cell(row=i, column=1, value=i)
            ws.cell(row=i, column=2, value=i*i)
        chart = BarChart()
        data = Reference(ws, min_col=2, min_row=1, max_row=10)
        chart.add_data(data)
        ws.add_chart(chart, "D1")
        wb.save(str(path))
        assert path.exists()
        return True, "xlsx图表正常"


def test_office_versions_compatibility():
    """测试Office版本兼容性标记"""
    # 验证生成的文件可以被不同版本Office打开
    # 这里通过验证文件格式标准来确认
    import zipfile
    with tempfile.TemporaryDirectory() as tmpdir:
        results = {}
        # docx
        path = Path(tmpdir) / "test.docx"
        create_test_word(path, "business")
        with zipfile.ZipFile(str(path)) as z:
            names = z.namelist()
            results["docx"] = "word/document.xml" in names
        # pptx
        path = Path(tmpdir) / "test.pptx"
        create_test_ppt(path, 3)
        with zipfile.ZipFile(str(path)) as z:
            names = z.namelist()
            results["pptx"] = any("ppt/slides/slide" in n for n in names)
        # xlsx
        path = Path(tmpdir) / "test.xlsx"
        create_test_excel(path, 50, 5)
        with zipfile.ZipFile(str(path)) as z:
            names = z.namelist()
            results["xlsx"] = "xl/workbook.xml" in names
        all_ok = all(results.values())
        return all_ok, f"Office格式兼容性: {results}"


def test_large_files():
    """测试大文件处理"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 大Word
        from docx import Document
        word_path = Path(tmpdir) / "large.docx"
        doc = Document()
        for i in range(100):
            doc.add_heading(f"章节{i}", level=1)
            for j in range(10):
                doc.add_paragraph(f"段落{j}内容" * 50)
        doc.save(str(word_path))
        word_size = word_path.stat().st_size / 1024
        # 大Excel
        from openpyxl import Workbook
        excel_path = Path(tmpdir) / "large.xlsx"
        wb = Workbook()
        ws = wb.active
        for r in range(1, 5001):
            for c in range(1, 21):
                ws.cell(row=r, column=c, value=r*c)
        wb.save(str(excel_path))
        excel_size = excel_path.stat().st_size / 1024
        # 大PPT
        from pptx import Presentation
        ppt_path = Path(tmpdir) / "large.pptx"
        prs = Presentation()
        for i in range(50):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            if slide.shapes.title:
                slide.shapes.title.text = f"幻灯片{i}"
        prs.save(str(ppt_path))
        ppt_size = ppt_path.stat().st_size / 1024
        return True, f"大文件: Word {word_size:.0f}KB, Excel {excel_size:.0f}KB, PPT {ppt_size:.0f}KB"


def run_office_compat_tests(runner: QATestRunner):
    print("\n=== Office Compatibility Tests ===")
    runner.run_test("docx读写", TestCategory.OFFICE_COMPAT, TestSeverity.CRITICAL, test_docx_read_write)
    runner.run_test("docx复杂结构", TestCategory.OFFICE_COMPAT, TestSeverity.HIGH, test_docx_complex_structure)
    runner.run_test("pptx读写", TestCategory.OFFICE_COMPAT, TestSeverity.CRITICAL, test_pptx_read_write)
    runner.run_test("pptx布局", TestCategory.OFFICE_COMPAT, TestSeverity.MEDIUM, test_pptx_layouts)
    runner.run_test("xlsx读写", TestCategory.OFFICE_COMPAT, TestSeverity.CRITICAL, test_xlsx_read_write)
    runner.run_test("xlsx公式", TestCategory.OFFICE_COMPAT, TestSeverity.HIGH, test_xlsx_formulas)
    runner.run_test("xlsx图表", TestCategory.OFFICE_COMPAT, TestSeverity.MEDIUM, test_xlsx_charts)
    runner.run_test("Office版本兼容", TestCategory.OFFICE_COMPAT, TestSeverity.HIGH, test_office_versions_compatibility)
    runner.run_test("大文件处理", TestCategory.OFFICE_COMPAT, TestSeverity.HIGH, test_large_files)


if __name__ == "__main__":
    runner = QATestRunner()
    runner.start()
    run_office_compat_tests(runner)
    runner.end()
    summary = runner.get_summary()
    print(f"\nResults: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']}%)")
