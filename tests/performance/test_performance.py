"""
Performance Tests - 性能和资源占用测试
测试CPU、内存、磁盘、任务耗时
场景: 100页PPT、10000行Excel、100页Word
"""
import os
import sys
import time
import tempfile
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.qa_framework import (
    TestCategory, TestSeverity, QATestRunner, PerformanceMetrics,
)


def perf_large_word():
    """性能: 100页Word"""
    from docx import Document
    with tempfile.TemporaryDirectory() as tmpdir:
        tracemalloc.start()
        start = time.time()
        path = Path(tmpdir) / "large.docx"
        doc = Document()
        for i in range(100):
            doc.add_heading(f"第{i+1}章 标题", level=1)
            for j in range(20):
                doc.add_paragraph(f"这是第{i+1}章第{j+1}段内容。" * 10)
        doc.save(str(path))
        duration = time.time() - start
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        size_kb = path.stat().st_size / 1024
        metrics = PerformanceMetrics(
            test_name="100页Word",
            duration_ms=int(duration * 1000),
            peak_memory_mb=peak / (1024*1024),
            file_size_kb=size_kb,
        )
        passed = duration < 30 and peak / (1024*1024) < 500
        return passed, f"100页Word: {duration:.2f}s, {peak/1024/1024:.1f}MB, {size_kb:.0f}KB", metrics.to_dict()


def perf_large_excel():
    """性能: 10000行Excel"""
    from openpyxl import Workbook
    with tempfile.TemporaryDirectory() as tmpdir:
        tracemalloc.start()
        start = time.time()
        path = Path(tmpdir) / "large.xlsx"
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("大数据")
        ws.append([f"列{i}" for i in range(20)])
        for r in range(10000):
            ws.append([r*20+c for c in range(20)])
        wb.save(str(path))
        duration = time.time() - start
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        size_kb = path.stat().st_size / 1024
        metrics = PerformanceMetrics(
            test_name="10000行Excel",
            duration_ms=int(duration * 1000),
            peak_memory_mb=peak / (1024*1024),
            file_size_kb=size_kb,
        )
        passed = duration < 30 and peak / (1024*1024) < 500
        return passed, f"10000行Excel: {duration:.2f}s, {peak/1024/1024:.1f}MB, {size_kb:.0f}KB", metrics.to_dict()


def perf_large_ppt():
    """性能: 100页PPT"""
    from pptx import Presentation
    with tempfile.TemporaryDirectory() as tmpdir:
        tracemalloc.start()
        start = time.time()
        path = Path(tmpdir) / "large.pptx"
        prs = Presentation()
        for i in range(100):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            if slide.shapes.title:
                slide.shapes.title.text = f"幻灯片 {i+1}"
            if len(slide.placeholders) > 1:
                slide.placeholders[1].text = f"内容 {i+1}"
        prs.save(str(path))
        duration = time.time() - start
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        size_kb = path.stat().st_size / 1024
        metrics = PerformanceMetrics(
            test_name="100页PPT",
            duration_ms=int(duration * 1000),
            peak_memory_mb=peak / (1024*1024),
            file_size_kb=size_kb,
        )
        passed = duration < 60 and peak / (1024*1024) < 500
        return passed, f"100页PPT: {duration:.2f}s, {peak/1024/1024:.1f}MB, {size_kb:.0f}KB", metrics.to_dict()


def run_performance_tests(runner: QATestRunner):
    print("\n=== Performance Tests ===")
    runner.run_test("100页Word性能", TestCategory.PERFORMANCE, TestSeverity.HIGH, perf_large_word)
    runner.run_test("10000行Excel性能", TestCategory.PERFORMANCE, TestSeverity.HIGH, perf_large_excel)
    runner.run_test("100页PPT性能", TestCategory.PERFORMANCE, TestSeverity.HIGH, perf_large_ppt)


if __name__ == "__main__":
    runner = QATestRunner()
    runner.start()
    run_performance_tests(runner)
    runner.end()
    summary = runner.get_summary()
    print(f"\nResults: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']}%)")
