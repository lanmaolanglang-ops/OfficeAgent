"""P1-22：两套 QA Runner 的判定口径必须一致，且失败不得被记成通过。

修复前行为（旧源码副本实测）：
``tests/test_report.py::TestRunner.run_test`` 的 else 分支把**任何**非
``None/True`` 的返回值都记成 PASS——测试函数显式 ``return False``、
``return (False, "msg")``、``return {"passed": False}`` 全部被判通过，
``return`` 值也恒为 ``True``，QA 通过率虚高；而 ``tests/qa_framework``
的 ``QATestRunner`` 对二元组/布尔/dict 有正确判定。两套 Runner 口径不一致。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.qa_framework import QATestRunner, TestCategory, TestSeverity
from tests.test_report import TestReport, TestRunner, TestStatus


def _run(returning):
    """把固定返回值包装成一个测试函数并按 report runner 跑一遍。"""
    runner = TestRunner()
    ok = runner.run_test("用例", "模块", "unit", lambda: returning)
    return ok, runner.report.results[-1]


class TestReportRunnerVerdicts:
    def test_none_is_pass(self):
        ok, result = _run(None)
        assert ok is True
        assert result.status is TestStatus.PASS

    def test_true_is_pass(self):
        ok, result = _run(True)
        assert ok is True
        assert result.status is TestStatus.PASS

    def test_false_is_fail(self):
        ok, result = _run(False)
        assert ok is False, "显式返回 False 必须判失败"
        assert result.status is TestStatus.FAIL

    def test_false_tuple_is_fail_with_message(self):
        ok, result = _run((False, "余额不足"))
        assert ok is False
        assert result.status is TestStatus.FAIL
        assert "余额不足" in result.error

    def test_true_tuple_is_pass(self):
        ok, result = _run((True, "ok"))
        assert ok is True
        assert result.status is TestStatus.PASS

    def test_empty_tuple_is_fail(self):
        ok, result = _run(())
        assert ok is False
        assert result.status is TestStatus.FAIL

    def test_dict_passed_false_is_fail(self):
        ok, result = _run({"passed": False, "message": "断言失败"})
        assert ok is False
        assert result.status is TestStatus.FAIL
        assert "断言失败" in result.error

    def test_dict_passed_true_is_pass(self):
        ok, result = _run({"passed": True, "message": "ok"})
        assert ok is True
        assert result.status is TestStatus.PASS

    def test_scoring_object_with_zero_score_is_fail(self):
        ok, result = _run(SimpleNamespace(passed=False, total_score=12.5))
        assert ok is False
        assert result.status is TestStatus.FAIL
        assert result.score == pytest.approx(12.5)

    def test_scoring_object_with_pass_is_pass(self):
        ok, result = _run(SimpleNamespace(passed=True, total_score=88.0))
        assert ok is True
        assert result.status is TestStatus.PASS
        assert result.score == pytest.approx(88.0)

    def test_other_truthy_value_keeps_legacy_pass(self):
        ok, result = _run("任意非布尔返回值")
        assert ok is True
        assert result.status is TestStatus.PASS

    def test_assertion_error_is_fail(self):
        def boom():
            raise AssertionError("断言不成立")

        runner = TestRunner()
        assert runner.run_test("用例", "模块", "unit", boom) is False
        assert runner.report.results[-1].status is TestStatus.FAIL

    def test_unexpected_exception_is_error(self):
        def boom():
            raise ValueError("坏数据")

        runner = TestRunner()
        assert runner.run_test("用例", "模块", "unit", boom) is False
        last = runner.report.results[-1]
        assert last.status is TestStatus.ERROR
        assert "ValueError" in last.error


class TestRunnerConsistency:
    @pytest.mark.parametrize("returning,expected_passed", [
        (None, True),
        (True, True),
        (False, False),
        ((True, "ok"), True),
        ((False, "bad"), False),
        ({"passed": True}, True),
        ({"passed": False}, False),
    ])
    def test_report_runner_matches_qa_framework_runner(self, returning, expected_passed):
        report_ok, report_result = _run(returning)
        qa_result = QATestRunner().run_test(
            "用例", TestCategory.UNIT, TestSeverity.CRITICAL, lambda: returning,
        )

        assert report_ok is expected_passed
        assert report_result.status is (
            TestStatus.PASS if expected_passed else TestStatus.FAIL
        )
        assert qa_result.passed is expected_passed
        # 两套 Runner 对同一返回值必须给出一致结论
        assert report_ok is qa_result.passed


class TestReportArithmetic:
    def test_pass_rate_reflects_failures(self):
        runner = TestRunner()
        runner.run_test("通过", "m", "unit", lambda: True)
        runner.run_test("失败", "m", "unit", lambda: False)
        runner.run_test("异常", "m", "unit", _raiser)
        report = runner.finish()

        assert report.total == 3
        assert report.passed == 1
        assert report.failed == 1
        assert report.errors == 1
        # 修复前 False 被记成 PASS，pass_rate 会是 66.7
        assert report.pass_rate == pytest.approx(100.0 / 3)
        assert report.to_dict()["summary"]["failed"] == 1

    def test_markdown_lists_real_failures(self):
        runner = TestRunner()
        runner.run_test("失败用例", "m", "unit", lambda: (False, "期望 A 得到 B"))
        report = runner.finish()
        markdown = report.to_markdown()
        assert "失败用例" in markdown
        assert "期望 A 得到 B" in markdown

    def test_no_check_level_skip_verdict_exists(self):
        """报告 Runner 无 SKIP 产出路径：不做"返回某值即跳过"的隐含语义。"""
        runner = TestRunner()
        runner.run_test("任意", "m", "unit", lambda: None)
        assert runner.report.skipped == 0


def _raiser():
    raise RuntimeError("boom")


class TestCollectionSafety:
    """锁定"根级 QA 库不会被 pytest 当成测试收集"这一契约。"""

    def test_module_import_has_no_side_effects_and_no_collected_tests(self):
        import inspect

        import tests.test_report as module

        test_classes = [
            name for name, obj in vars(module).items()
            if name.startswith("Test") and inspect.isclass(obj)
        ]
        assert test_classes, "应能识别到 Test* 类"
        for name in test_classes:
            assert getattr(getattr(module, name), "__test__", True) is False, (
                f"{name} 必须显式 __test__ = False，否则会被 pytest 收集成测试类"
            )

        collected = [
            name for name, obj in vars(module).items()
            if name.startswith("test_") and callable(obj)
        ]
        assert collected == []

    def test_report_dataclass_still_works_after_verdict_fix(self):
        report = TestReport(project="Office Agent", version="0.0.0")
        assert report.total == 0
        assert report.pass_rate == 0
        assert "Office Agent" in report.to_markdown()
