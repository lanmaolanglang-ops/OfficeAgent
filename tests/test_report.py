"""
测试报告生成系统
生成测试结果报告（JSON + 控制台 + Markdown）
"""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from enum import Enum


class TestStatus(str, Enum):
    __test__ = False
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"


@dataclass
class TestCaseResult:
    __test__ = False
    """单个测试用例结果"""
    name: str
    module: str
    category: str  # unit/integration/agent/performance
    status: TestStatus
    duration: float = 0.0
    error: str = ""
    score: float | None = None  # Agent评估分数
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "module": self.module,
            "category": self.category,
            "status": self.status.value,
            "duration": round(self.duration, 4),
            "error": self.error,
            "score": self.score,
            "details": self.details,
        }


@dataclass
class TestReport:
    __test__ = False
    """测试报告"""
    project: str = "Office Agent"
    version: str = "0.45.0"
    start_time: float = field(default_factory=time.time)
    end_time: float = 0
    results: list[TestCaseResult] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time if self.end_time else 0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.FAIL)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.ERROR)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.SKIP)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total * 100 if self.total else 0

    def add_result(self, result: TestCaseResult):
        self.results.append(result)

    def finish(self):
        self.end_time = time.time()

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "version": self.version,
            "timestamp": datetime.fromtimestamp(self.start_time).isoformat(),
            "duration_s": round(self.duration, 2),
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "errors": self.errors,
                "skipped": self.skipped,
                "pass_rate": round(self.pass_rate, 2),
            },
            "by_category": self._by_category(),
            "results": [r.to_dict() for r in self.results],
        }

    def _by_category(self) -> dict:
        cats = {}
        for r in self.results:
            if r.category not in cats:
                cats[r.category] = {"total": 0, "passed": 0, "failed": 0, "avg_duration": 0}
            cats[r.category]["total"] += 1
            if r.status == TestStatus.PASS:
                cats[r.category]["passed"] += 1
            elif r.status in (TestStatus.FAIL, TestStatus.ERROR):
                cats[r.category]["failed"] += 1
            cats[r.category]["avg_duration"] += r.duration
        for cat in cats.values():
            cat["avg_duration"] = round(cat["avg_duration"] / cat["total"], 4) if cat["total"] else 0
        return cats

    def to_json(self, path: str | Path = None) -> str:
        data = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        if path:
            Path(path).write_text(data, encoding="utf-8")
        return data

    def to_markdown(self) -> str:
        """生成Markdown格式报告"""
        lines = [
            f"# {self.project} 测试报告",
            "",
            f"- **版本**: {self.version}",
            f"- **时间**: {datetime.fromtimestamp(self.start_time).strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **耗时**: {self.duration:.2f}秒",
            "",
            "## 概要",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 总用例 | {self.total} |",
            f"| ✅ 通过 | {self.passed} |",
            f"| ❌ 失败 | {self.failed} |",
            f"| ⚠️ 错误 | {self.errors} |",
            f"| ⏭️ 跳过 | {self.skipped} |",
            f"| **通过率** | **{self.pass_rate:.1f}%** |",
            "",
            "## 分类统计",
            "",
            "| 分类 | 总数 | 通过 | 失败 | 平均耗时 |",
            "|------|------|------|------|----------|",
        ]
        for cat, stats in self._by_category().items():
            lines.append(
                f"| {cat} | {stats['total']} | {stats['passed']} | {stats['failed']} | {stats['avg_duration']*1000:.1f}ms |"
            )

        # 失败用例
        failures = [r for r in self.results if r.status in (TestStatus.FAIL, TestStatus.ERROR)]
        if failures:
            lines.extend(["", "## 失败用例", ""])
            for r in failures:
                lines.append(f"### ❌ {r.module}::{r.name}")
                lines.append(f"- 分类: {r.category}")
                lines.append(f"- 错误: `{r.error[:200]}`")
                lines.append("")

        return "\n".join(lines)

    def print_summary(self):
        """打印控制台摘要"""
        print("\n" + "=" * 60)
        print(f"  {self.project} v{self.version} 测试报告")
        print("=" * 60)
        print(f"  总用例: {self.total}")
        print(f"  ✅ 通过: {self.passed}")
        print(f"  ❌ 失败: {self.failed}")
        print(f"  ⚠️ 错误: {self.errors}")
        print(f"  ⏭️ 跳过: {self.skipped}")
        print(f"  通过率: {self.pass_rate:.1f}%")
        print(f"  耗时: {self.duration:.2f}s")
        print("=" * 60)

        for cat, stats in self._by_category().items():
            print(f"  {cat:15s}: {stats['passed']}/{stats['total']} passed")
        print("=" * 60)


class TestRunner:
    __test__ = False
    """测试运行器"""

    def __init__(self, project: str = "Office Agent", version: str = "0.45.0"):
        self.report = TestReport(project=project, version=version)

    @staticmethod
    def _judge_result(result) -> tuple[TestStatus, float | None]:
        """把测试函数的返回值映射为状态。

        判定口径必须与 :class:`tests.qa_framework.QATestRunner` 一致，
        否则同一份用例在两套 Runner 下会得出不同通过率：

        - 返回带 ``total_score`` / ``passed`` 的评测对象 → 按 ``passed`` 判定
        - 返回 ``(ok, msg)`` 元组 → 按第一个元素判定
        - 返回 ``dict`` → 按 ``passed`` 键判定（缺省 True）
        - 返回 ``bool`` → 按值判定（**False 即 FAIL**）
        - 返回 ``None`` 或其它非布尔值 → PASS（"无断言即通过"的历史语义）

        修复前：``result is False`` 与 ``(False, msg)`` 元组都落到 else 分支被
        无条件记为 PASS，测试函数显式返回失败仍算通过，QA 通过率虚高。
        """
        if hasattr(result, "total_score"):
            passed = bool(getattr(result, "passed", False))
            return (TestStatus.PASS if passed else TestStatus.FAIL), result.total_score
        if isinstance(result, tuple):
            if not result:
                return TestStatus.FAIL, None
            head = result[0]
            if isinstance(head, bool):
                return (TestStatus.PASS if head else TestStatus.FAIL), None
            return TestStatus.PASS, None
        if isinstance(result, dict):
            passed = result.get("passed", True)
            return (TestStatus.PASS if passed else TestStatus.FAIL), None
        if isinstance(result, bool):
            return (TestStatus.PASS if result else TestStatus.FAIL), None
        return TestStatus.PASS, None

    @staticmethod
    def _failure_note(result) -> str:
        """为 FAIL 结果补充可读原因（Markdown 报告会打印 error 字段）。"""
        if isinstance(result, tuple) and len(result) > 1:
            return str(result[1])
        if isinstance(result, dict):
            return str(result.get("message", ""))
        return "测试函数返回失败"

    def run_test(self, name: str, module: str, category: str, func, *args, **kwargs):
        """运行单个测试，返回该用例是否通过。"""
        start = time.time()
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start
            status, score = self._judge_result(result)
            self.report.add_result(TestCaseResult(
                name=name, module=module, category=category,
                status=status, duration=duration, score=score,
                error="" if status == TestStatus.PASS else self._failure_note(result),
            ))
            return status == TestStatus.PASS
        except AssertionError as e:
            duration = time.time() - start
            self.report.add_result(TestCaseResult(
                name=name, module=module, category=category,
                status=TestStatus.FAIL, duration=duration, error=str(e),
            ))
            return False
        except Exception as e:
            duration = time.time() - start
            self.report.add_result(TestCaseResult(
                name=name, module=module, category=category,
                status=TestStatus.ERROR, duration=duration,
                error=f"{type(e).__name__}: {e}",
            ))
            return False

    def finish(self) -> TestReport:
        self.report.finish()
        return self.report
