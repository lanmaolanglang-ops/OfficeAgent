"""
QA Report Generator - 自动测试报告生成器
生成 QA_Report.md，包含测试项目、通过率、失败原因、性能数据、建议
"""
import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.qa_framework import QATestRunner, TestCategory


def generate_report(runner: QATestRunner, eval_results: dict = None, output_path: str = None) -> str:
    """生成QA报告"""
    summary = runner.get_summary()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append("# OfficeAgent QA 测试报告")
    lines.append("")
    lines.append(f"**版本**: v0.48.0")
    lines.append(f"**测试时间**: {now}")
    lines.append(f"**测试环境**: Python {sys.version.split()[0]} / {sys.platform}")
    lines.append("")
    # 总体结果
    status_icon = "✅" if not summary["release_blocked"] else "❌"
    lines.append("## 总体结果")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 总体状态 | {status_icon} {'通过' if not summary['release_blocked'] else '阻塞发布'} |")
    lines.append(f"| 测试总数 | {summary['total']} |")
    lines.append(f"| 通过 | {summary['passed']} |")
    lines.append(f"| 失败 | {summary['failed']} |")
    lines.append(f"| 通过率 | {summary['pass_rate']}% |")
    lines.append(f"| 平均分数 | {summary['avg_score']} |")
    lines.append(f"| 严重失败 | {summary['critical_failed']} |")
    lines.append(f"| 总耗时 | {summary['total_duration_ms']/1000:.1f}s |")
    lines.append("")
    # 分类结果
    lines.append("## 分类结果")
    lines.append("")
    lines.append("| 分类 | 总数 | 通过 | 失败 | 通过率 |")
    lines.append("|------|------|------|------|--------|")
    cat_names = {
        "unit": "单元测试",
        "integration": "集成测试",
        "agent": "Agent测试",
        "office_compatibility": "Office兼容",
        "runtime": "运行时",
        "performance": "性能测试",
        "recovery": "异常恢复",
    }
    for cat, stats in summary["by_category"].items():
        name = cat_names.get(cat, cat)
        rate = round(stats["passed"] / stats["total"] * 100, 1) if stats["total"] else 0
        icon = "✅" if stats["failed"] == 0 else "❌"
        lines.append(f"| {icon} {name} | {stats['total']} | {stats['passed']} | {stats['failed']} | {rate}% |")
    lines.append("")
    # 严重级别
    lines.append("## 严重级别统计")
    lines.append("")
    lines.append("| 级别 | 总数 | 通过 | 失败 |")
    lines.append("|------|------|------|------|")
    sev_names = {"critical": "🔴 严重", "high": "🟠 高", "medium": "🟡 中", "low": "🟢 低"}
    for sev, stats in summary["by_severity"].items():
        name = sev_names.get(sev, sev)
        lines.append(f"| {name} | {stats['total']} | {stats['passed']} | {stats['failed']} |")
    lines.append("")
    # 失败用例
    failed = runner.get_failed_tests()
    if failed:
        lines.append("## 失败用例详情")
        lines.append("")
        for r in failed:
            lines.append(f"### ❌ {r.test_name}")
            lines.append(f"- **分类**: {r.category.value}")
            lines.append(f"- **严重级别**: {r.severity.value}")
            lines.append(f"- **耗时**: {r.duration_ms}ms")
            if r.message:
                lines.append(f"- **消息**: {r.message}")
            if r.error:
                lines.append(f"- **错误**: {r.error}")
            lines.append("")
    else:
        lines.append("## 失败用例详情")
        lines.append("")
        lines.append("✅ 所有测试通过！")
        lines.append("")
    # Agent评分
    if eval_results:
        lines.append("## Agent 评分")
        lines.append("")
        lines.append(f"**总体评分**: {eval_results.get('overall', 0)}分")
        lines.append("")
        if eval_results.get("by_type"):
            lines.append("| Agent类型 | 平均分 |")
            lines.append("|-----------|--------|")
            type_names = {"word": "Word Agent", "ppt": "PPT Agent", "excel": "Excel Agent"}
            for t, score in eval_results["by_type"].items():
                name = type_names.get(t, t)
                icon = "✅" if score >= 80 else ("⚠️" if score >= 60 else "❌")
                lines.append(f"| {icon} {name} | {score} |")
            lines.append("")
    # 性能数据
    perf_results = [r for r in runner.results if r.category == TestCategory.PERFORMANCE]
    if perf_results:
        lines.append("## 性能数据")
        lines.append("")
        lines.append("| 测试项 | 结果 | 详情 |")
        lines.append("|--------|------|------|")
        for r in perf_results:
            icon = "✅" if r.passed else "❌"
            lines.append(f"| {icon} {r.test_name} | {'通过' if r.passed else '失败'} | {r.message} |")
        lines.append("")
    # 建议
    lines.append("## 建议")
    lines.append("")
    recommendations = []
    if summary["critical_failed"] > 0:
        recommendations.append("🔴 **存在严重级别失败，建议修复后再发布**")
    if summary["pass_rate"] < 90:
        recommendations.append(f"⚠️ 通过率 {summary['pass_rate']}% 低于90%，建议检查失败用例")
    if summary["avg_score"] < 80:
        recommendations.append(f"⚠️ 平均分数 {summary['avg_score']} 低于80分")
    # 性能建议
    for r in perf_results:
        if not r.passed:
            recommendations.append(f"⚡ 性能问题: {r.test_name} - {r.message}")
    if not recommendations:
        recommendations.append("✅ 系统状态良好，可以发布")
    for rec in recommendations:
        lines.append(f"- {rec}")
    lines.append("")
    # 附录
    lines.append("## 附录")
    lines.append("")
    lines.append("### 测试范围")
    lines.append("- 单元测试: 基础功能验证")
    lines.append("- 集成测试: 真实场景完整流程")
    lines.append("- Agent测试: Word/PPT/Excel Agent功能")
    lines.append("- Office兼容: docx/pptx/xlsx格式兼容")
    lines.append("- Runtime: 本地运行时生命周期")
    lines.append("- 性能: 大文件处理、并发、资源限制")
    lines.append("- 异常恢复: 失败重试、损坏文件、网络错误")
    lines.append("")
    lines.append("---")
    lines.append(f"*报告由 OfficeAgent QA System 自动生成*")
    report = "\n".join(lines)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
    return report


if __name__ == "__main__":
    # 简单测试报告生成
    runner = QATestRunner()
    runner.start()
    runner._start_time = time.time() - 10
    runner._end_time = time.time()
    report = generate_report(runner, output_path=str(PROJECT_ROOT / "QA_Report.md"))
    print(report[:500])
