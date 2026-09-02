"""
Quality Scoring Engine - 统一质量评分引擎

根据文件类型自动选择评分器，输出0-100分：

Word:
- 格式正确率 (format_accuracy)
- 标题识别率 (heading_recognition)
- 排版一致性 (layout_consistency)

PPT:
- 视觉评分 (visual_score)
- 内容完整度 (content_completeness)
- 模板遵循度 (template_adherence)

Excel:
- 公式正确率 (formula_accuracy)
- 分析准确率 (analysis_accuracy)
- 图表合理性 (chart_appropriateness)

使用示例：
    from office_agent.quality_scoring import QualityScoringEngine

    engine = QualityScoringEngine()

    # 自动识别文件类型
    result = engine.score_file("report.docx")
    print(f"总分: {result.total_score}")
    print(f"各维度: {result.scores}")

    # 带期望要求
    result = engine.score_file(
        "analysis.xlsx",
        expected_formulas=["SUM", "IF"],
        expected_charts=["柱状图"],
        expected_sheets=["月度汇总", "分析结论"]
    )
"""
import os
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, field, asdict

from .word_scorer import WordQualityScorer, WordScoreResult
from .ppt_scorer import PPTQualityScorer, PPTScoreResult
from .excel_scorer import ExcelQualityScorer, ExcelScoreResult


@dataclass
class ScoreResult:
    """统一评分结果"""
    file_path: str
    file_type: str           # word/ppt/excel
    total_score: float       # 0-100

    # 各维度分数
    scores: Dict[str, float] = field(default_factory=dict)

    # 各维度名称映射
    dimension_names: Dict[str, str] = field(default_factory=dict)

    # 详细信息
    details: Dict[str, Any] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    # 等级
    grade: str = ""
    passed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_type": self.file_type,
            "total_score": round(self.total_score, 1),
            "scores": {k: round(v, 1) for k, v in self.scores.items()},
            "dimension_names": self.dimension_names,
            "details": self.details,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "grade": self.grade,
            "passed": self.passed,
        }

    def summary(self) -> str:
        """生成文本摘要"""
        lines = [
            f"文件: {os.path.basename(self.file_path)}",
            f"类型: {self.file_type.upper()}",
            f"总分: {self.total_score:.1f}/100  {self.grade}  {'通过' if self.passed else '未通过'}",
            "",
            "各维度评分:",
        ]
        for key, score in self.scores.items():
            name = self.dimension_names.get(key, key)
            bar = self._score_bar(score)
            lines.append(f"  {name}: {bar} {score:.1f}")

        if self.issues:
            lines.append("")
            lines.append("问题:")
            for issue in self.issues[:5]:
                lines.append(f"  - {issue}")

        if self.suggestions:
            lines.append("")
            lines.append("建议:")
            for sug in self.suggestions[:5]:
                lines.append(f"  - {sug}")

        return "\n".join(lines)

    @staticmethod
    def _score_bar(score: float, width: int = 20) -> str:
        filled = int(score / 100 * width)
        return "[" + "█" * filled + "░" * (width - filled) + "]"


class QualityScoringEngine:
    """统一质量评分引擎"""

    # 文件扩展名映射
    EXT_MAP = {
        ".docx": "word",
        ".pptx": "ppt",
        ".xlsx": "excel",
    }

    # 维度名称
    DIMENSION_NAMES = {
        "word": {
            "format_accuracy": "格式正确率",
            "heading_recognition": "标题识别率",
            "layout_consistency": "排版一致性",
        },
        "ppt": {
            "visual_score": "视觉评分",
            "content_completeness": "内容完整度",
            "template_adherence": "模板遵循度",
        },
        "excel": {
            "formula_accuracy": "公式正确率",
            "analysis_accuracy": "分析准确率",
            "chart_appropriateness": "图表合理性",
        },
    }

    def __init__(self, pass_threshold: float = 60.0):
        self.pass_threshold = pass_threshold
        self._word_scorer = WordQualityScorer()
        self._ppt_scorer = PPTQualityScorer()
        self._excel_scorer = ExcelQualityScorer()

    def score_file(self, file_path: str, **kwargs) -> ScoreResult:
        """
        评分文件，自动识别类型

        Args:
            file_path: 文件路径
            **kwargs: 传给具体评分器的参数
                Word: expected_format
                PPT: expected_slides, required_content
                Excel: expected_formulas, expected_charts, expected_sheets
        """
        if not os.path.exists(file_path):
            return ScoreResult(
                file_path=file_path,
                file_type="unknown",
                total_score=0,
                issues=[f"文件不存在: {file_path}"],
            )

        file_type = self._detect_type(file_path)
        if file_type == "unknown":
            return ScoreResult(
                file_path=file_path,
                file_type="unknown",
                total_score=0,
                issues=[f"不支持的文件类型: {os.path.splitext(file_path)[1]}"],
            )

        # 调用对应评分器
        if file_type == "word":
            raw = self._word_scorer.score(
                file_path,
                expected_format=kwargs.get("expected_format")
            )
            return self._wrap_word_result(raw)

        elif file_type == "ppt":
            raw = self._ppt_scorer.score(
                file_path,
                expected_slides=kwargs.get("expected_slides", 0),
                required_content=kwargs.get("required_content"),
            )
            return self._wrap_ppt_result(raw)

        elif file_type == "excel":
            raw = self._excel_scorer.score(
                file_path,
                expected_formulas=kwargs.get("expected_formulas"),
                expected_charts=kwargs.get("expected_charts"),
                expected_sheets=kwargs.get("expected_sheets"),
            )
            return self._wrap_excel_result(raw)

    def score_multiple(self, file_paths: List[str], **kwargs) -> List[ScoreResult]:
        """批量评分"""
        return [self.score_file(fp, **kwargs) for fp in file_paths]

    def _detect_type(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        return self.EXT_MAP.get(ext, "unknown")

    def _wrap_word_result(self, raw: WordScoreResult) -> ScoreResult:
        scores = {
            "format_accuracy": raw.format_accuracy,
            "heading_recognition": raw.heading_recognition,
            "layout_consistency": raw.layout_consistency,
        }
        result = ScoreResult(
            file_path=raw.file_path,
            file_type="word",
            total_score=raw.total_score,
            scores=scores,
            dimension_names=self.DIMENSION_NAMES["word"],
            details={
                "format": raw.format_details,
                "heading": raw.heading_details,
                "layout": raw.layout_details,
            },
            issues=raw.issues,
            suggestions=raw.suggestions,
        )
        self._apply_grade(result)
        return result

    def _wrap_ppt_result(self, raw: PPTScoreResult) -> ScoreResult:
        scores = {
            "visual_score": raw.visual_score,
            "content_completeness": raw.content_completeness,
            "template_adherence": raw.template_adherence,
        }
        result = ScoreResult(
            file_path=raw.file_path,
            file_type="ppt",
            total_score=raw.total_score,
            scores=scores,
            dimension_names=self.DIMENSION_NAMES["ppt"],
            details={
                "visual": raw.visual_details,
                "content": raw.content_details,
                "template": raw.template_details,
            },
            issues=raw.issues,
            suggestions=raw.suggestions,
        )
        self._apply_grade(result)
        return result

    def _wrap_excel_result(self, raw: ExcelScoreResult) -> ScoreResult:
        scores = {
            "formula_accuracy": raw.formula_accuracy,
            "analysis_accuracy": raw.analysis_accuracy,
            "chart_appropriateness": raw.chart_appropriateness,
        }
        result = ScoreResult(
            file_path=raw.file_path,
            file_type="excel",
            total_score=raw.total_score,
            scores=scores,
            dimension_names=self.DIMENSION_NAMES["excel"],
            details={
                "formula": raw.formula_details,
                "analysis": raw.analysis_details,
                "chart": raw.chart_details,
            },
            issues=raw.issues,
            suggestions=raw.suggestions,
        )
        self._apply_grade(result)
        return result

    def _apply_grade(self, result: ScoreResult):
        """应用等级"""
        score = result.total_score
        if score >= 90:
            result.grade = "优秀 ★★★★★"
        elif score >= 75:
            result.grade = "良好 ★★★★☆"
        elif score >= 60:
            result.grade = "合格 ★★★☆☆"
        elif score >= 40:
            result.grade = "较差 ★★☆☆☆"
        else:
            result.grade = "不合格 ★☆☆☆☆"
        result.passed = score >= self.pass_threshold


def score_file(file_path: str, **kwargs) -> ScoreResult:
    """便捷函数：评分单个文件"""
    engine = QualityScoringEngine()
    return engine.score_file(file_path, **kwargs)
