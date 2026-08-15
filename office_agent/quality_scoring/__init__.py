"""
Quality Scoring Engine - 质量评分引擎

对生成的 Office 文件进行实际质量评分，输出 0-100 分。

评分维度：

Word:
- 格式正确率 (format_accuracy): 字体、字号、对齐、行距、缩进
- 标题识别率 (heading_recognition): 标题层级、样式使用
- 排版一致性 (layout_consistency): 同类元素格式统一

PPT:
- 视觉评分 (visual_score): 配色、布局、字体、设计感
- 内容完整度 (content_completeness): 页数、标题、内容量
- 模板遵循度 (template_adherence): 字体/颜色/位置一致性

Excel:
- 公式正确率 (formula_accuracy): 公式数量、类型、无错误
- 分析准确率 (analysis_accuracy): 多Sheet、分析表、表头
- 图表合理性 (chart_appropriateness): 图表数量、类型

使用示例：
    from office_agent.quality_scoring import QualityScoringEngine, score_file

    engine = QualityScoringEngine()

    # 自动识别文件类型
    result = engine.score_file("report.docx")
    print(result.total_score)      # 0-100
    print(result.scores)           # 各维度分数
    print(result.grade)            # 等级
    print(result.summary())        # 文本报告

    # 带期望要求
    result = engine.score_file(
        "data.xlsx",
        expected_formulas=["SUM", "AVERAGE", "IF"],
        expected_charts=["柱状图"],
        expected_sheets=["汇总", "分析"]
    )
"""

from .word_scorer import WordQualityScorer, WordScoreResult
from .ppt_scorer import PPTQualityScorer, PPTScoreResult
from .excel_scorer import ExcelQualityScorer, ExcelScoreResult
from .scoring_engine import (
    QualityScoringEngine,
    ScoreResult,
    score_file,
)

__all__ = [
    "WordQualityScorer",
    "WordScoreResult",
    "PPTQualityScorer",
    "PPTScoreResult",
    "ExcelQualityScorer",
    "ExcelScoreResult",
    "QualityScoringEngine",
    "ScoreResult",
    "score_file",
]
