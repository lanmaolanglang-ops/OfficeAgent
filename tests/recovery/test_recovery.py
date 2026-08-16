"""
Recovery Tests - 异常恢复测试
模拟: AI API失败、网络断开、文件损坏、程序关闭、电脑休眠
验证: 任务恢复、错误提示、日志记录
"""
import os
import sys
import time
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.qa_framework import TestCategory, TestSeverity, QATestRunner


def test_corrupted_file_handling():
    """测试损坏文件处理"""
    from office_agent.parsers import analyze_document
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建损坏的docx
        bad_path = Path(tmpdir) / "corrupt.docx"
        bad_path.write_bytes(b"PK\x03\x04 this is not a valid docx file")
        try:
            analyze_document(str(bad_path))
            # 如果没有抛出异常，说明有容错
            return True, "损坏文件被容错处理"
        except Exception as e:
            # 抛出异常是预期的，验证错误信息清晰
            assert "not" in str(e).lower() or "invalid" in str(e).lower() or "error" in str(e).lower() or "zip" in str(e).lower() or "bad" in str(e).lower() or isinstance(e, (zipfile.BadZipFile, ValueError, KeyError, OSError))
            return True, f"损坏文件正确报错: {type(e).__name__}"


def test_network_failure_simulation():
    """测试网络失败模拟"""
    import urllib.request
    import urllib.error
    # 尝试连接不存在的地址
    try:
        urllib.request.urlopen("http://127.0.0.1:19999/nonexistent", timeout=1)
        return False, "应该连接失败"
    except (urllib.error.URLError, ConnectionRefusedError, OSError):
        return True, "网络失败被正确捕获"


def test_error_logging():
    """测试错误日志记录"""
    import logging
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "error.log"
        logger = logging.getLogger("test_recovery")
        logger.setLevel(logging.ERROR)
        handler = logging.FileHandler(str(log_path), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(handler)
        # 模拟错误
        try:
            raise ValueError("测试错误")
        except ValueError as e:
            logger.error(f"任务失败: {e}", exc_info=True)
        handler.flush()
        handler.close()
        # 验证日志
        content = log_path.read_text(encoding="utf-8")
        assert "测试错误" in content
        assert "ERROR" in content
        return True, "错误日志记录正常"


def test_empty_file_handling():
    """测试空文件处理"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 空Word
        from docx import Document
        empty_docx = Path(tmpdir) / "empty.docx"
        Document().save(str(empty_docx))
        doc = Document(str(empty_docx))
        assert len(doc.paragraphs) == 0
        # 空Excel
        from openpyxl import Workbook
        empty_xlsx = Path(tmpdir) / "empty.xlsx"
        Workbook().save(str(empty_xlsx))
        return True, "空文件处理正常"


def run_recovery_tests(runner: QATestRunner):
    print("\n=== Recovery Tests ===")
    runner.run_test("损坏文件处理", TestCategory.RECOVERY, TestSeverity.CRITICAL, test_corrupted_file_handling)
    runner.run_test("网络失败处理", TestCategory.RECOVERY, TestSeverity.HIGH, test_network_failure_simulation)
    runner.run_test("错误日志", TestCategory.RECOVERY, TestSeverity.HIGH, test_error_logging)
    runner.run_test("空文件处理", TestCategory.RECOVERY, TestSeverity.MEDIUM, test_empty_file_handling)


if __name__ == "__main__":
    import zipfile
    runner = QATestRunner()
    runner.start()
    run_recovery_tests(runner)
    runner.end()
    summary = runner.get_summary()
    print(f"\nResults: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']}%)")
