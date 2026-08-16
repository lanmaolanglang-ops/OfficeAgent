"""
Regression Test Runner - 回归测试运行器
每次更新自动运行核心测试集，防止新功能破坏旧功能
"""
import os
import sys
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.qa_framework import QATestRunner, TestCategory, TestSeverity
from tests.qa_report import generate_report


def run_pytest_suite(test_path: str = "tests/", pattern: str = None) -> dict:
    """运行pytest测试套件"""
    cmd = [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short", "-q"]
    if pattern:
        cmd.extend(["-k", pattern])
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        duration = time.time() - start
        # 解析结果
        output = result.stdout + result.stderr
        passed = 0
        failed = 0
        errors = 0
        for line in output.split("\n"):
            if " passed" in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "passed":
                        try:
                            passed = int(parts[i-1])
                        except ValueError:
                            pass
                    elif p == "failed":
                        try:
                            failed = int(parts[i-1])
                        except ValueError:
                            pass
                    elif p == "error":
                        try:
                            errors = int(parts[i-1])
                        except ValueError:
                            pass
        return {
            "success": result.returncode == 0,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "duration_seconds": round(duration, 2),
            "output": output[-2000:] if output else "",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "passed": 0, "failed": 0, "errors": 1, "duration_seconds": 300, "output": "Timeout"}
    except Exception as e:
        return {"success": False, "passed": 0, "failed": 0, "errors": 1, "duration_seconds": 0, "output": str(e)}


def run_qa_suite(include_performance: bool = False) -> QATestRunner:
    """运行QA测试套件"""
    runner = QATestRunner()
    runner.start()
    # Office兼容
    try:
        from tests.office_compatibility.test_office_compat import run_office_compat_tests
        run_office_compat_tests(runner)
    except Exception as e:
        print(f"Office compat tests error: {e}")
    # 恢复
    try:
        from tests.recovery.test_recovery import run_recovery_tests
        import zipfile
        run_recovery_tests(runner)
    except Exception as e:
        print(f"Recovery tests error: {e}")
    # 性能（可选）
    if include_performance:
        try:
            from tests.performance.test_performance import run_performance_tests
            run_performance_tests(runner)
        except Exception as e:
            print(f"Performance tests error: {e}")
    runner.end()
    return runner


def run_regression(include_performance: bool = False, should_generate_report: bool = True) -> dict:
    """运行完整回归测试"""
    print("=" * 60)
    print("OfficeAgent Regression Test Suite v0.50.0")
    print("=" * 60)
    results = {
        "version": "0.50.0",
        "timestamp": datetime.now().isoformat(),
        "pytest": None,
        "qa": None,
        "evaluation": None,
        "blocked": False,
    }
    # 1. 生成测试数据
    print("\n[1/4] Generating test dataset...")
    try:
        from tests.generate_dataset import generate_dataset
        generate_dataset()
    except Exception as e:
        print(f"  Dataset generation error: {e}")
    # 2. 运行pytest
    print("\n[2/4] Running pytest suite...")
    pytest_result = run_pytest_suite("tests/unit/")
    results["pytest"] = pytest_result
    print(f"  pytest: {pytest_result['passed']} passed, {pytest_result['failed']} failed, {pytest_result['errors']} errors")
    # 3. 运行QA套件
    print("\n[3/4] Running QA suite...")
    qa_runner = run_qa_suite(include_performance=include_performance)
    qa_summary = qa_runner.get_summary()
    results["qa"] = qa_summary
    print(f"  QA: {qa_summary['passed']}/{qa_summary['total']} passed ({qa_summary['pass_rate']}%)")
    # 4. Agent评估
    print("\n[4/4] Running Agent evaluation...")
    try:
        from tests.agent.test_evaluation import run_evaluation_tests
        eval_suite = run_evaluation_tests()
        results["evaluation"] = eval_suite.get_summary()
    except Exception as e:
        print(f"  Evaluation error: {e}")
        results["evaluation"] = {"error": str(e)}
    # 判断是否阻塞
    if qa_summary.get("critical_failed", 0) > 0 or not pytest_result["success"]:
        results["blocked"] = True
    # 生成报告
    if should_generate_report:
        report_path = str(PROJECT_ROOT / "QA_Report.md")
        eval_data = results.get("evaluation", {})
        if isinstance(eval_data, dict) and "overall" in eval_data:
            generate_report(qa_runner, eval_results=eval_data, output_path=report_path)
        else:
            generate_report(qa_runner, output_path=report_path)
        print(f"\nReport generated: {report_path}")
    # 保存JSON结果
    results_path = PROJECT_ROOT / "tests" / "regression_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    # 总结
    print("\n" + "=" * 60)
    if results["blocked"]:
        print("❌ REGRESSION FAILED - 发布阻塞")
    else:
        print("✅ REGRESSION PASSED - 可以发布")
    print("=" * 60)
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OfficeAgent Regression Test Runner")
    parser.add_argument("--performance", action="store_true", help="Include performance tests")
    parser.add_argument("--no-report", action="store_true", help="Skip report generation")
    args = parser.parse_args()
    run_regression(include_performance=args.performance, should_generate_report=not args.no_report)
