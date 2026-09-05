"""
PPT Quality Checker - PPT 质量检查与自动修正

检查项：
1. 文字溢出 - 估算文字是否超出文本框边界
2. 布局合理 - 元素是否超出页面、重叠、位置异常
3. 字体统一 - 全文档字体种类、每页字体一致性
4. 页数合规 - 页数是否符合要求范围
5. 模板遵循 - 是否遵循模板配置（颜色/字体/位置）
6. 内容完整 - 标题不为空、要点数量合理、无空白页

发现问题后：
- 可自动修正的问题返回修正建议
- 调用 fix() 方法自动修正并重新生成
"""
import copy
import logging
import math
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass, field

from pptx import Presentation

from .models import PPTOutline, SlideContent
from ..quality.checker import IssueSeverity

logger = logging.getLogger(__name__)


# ==========================================
# 数据结构
# ==========================================

@dataclass
class PPTQualityIssue:
    """PPT 质量问题"""
    slide_index: int          # 第几页（0-based），-1 表示全局
    issue_type: str           # overflow/layout/font/count/template/content
    severity: str             # error/warning/info
    message: str
    detail: str = ""
    fixable: bool = True
    fix_action: dict = field(default_factory=dict)  # 修正动作

    def __post_init__(self):
        # 构造边界统一校验：枚举是唯一权威，未知 severity 不得静默漂移
        self.severity = IssueSeverity.normalize(self.severity)

    def to_dict(self) -> dict:
        return {
            "slide": self.slide_index + 1 if self.slide_index >= 0 else 0,
            "type": self.issue_type,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
            "fixable": self.fixable,
        }


@dataclass
class PPTQualityReport:
    """PPT 质量报告"""
    file_path: str = ""
    slide_count: int = 0
    issues: List[PPTQualityIssue] = field(default_factory=list)
    score: float = 100.0
    passed: bool = True
    expected_slides: Optional[int] = None

    def compute_score(self):
        errors = sum(1 for i in self.issues if i.severity == IssueSeverity.ERROR.value)
        warnings = sum(1 for i in self.issues if i.severity == IssueSeverity.WARNING.value)
        infos = sum(1 for i in self.issues if i.severity == IssueSeverity.INFO.value)
        self.score = max(0.0, 100.0 - errors * 10 - warnings * 3 - infos * 1)
        self.passed = errors == 0

    def errors(self) -> list:
        return [i for i in self.issues if i.severity == IssueSeverity.ERROR.value]

    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == IssueSeverity.WARNING.value]

    def fixable_issues(self) -> list:
        return [i for i in self.issues if i.fixable]

    def get_fix_actions(self) -> List[dict]:
        """获取所有修正动作"""
        actions = []
        for issue in self.fixable_issues():
            if issue.fix_action:
                actions.append(issue.fix_action)
        return actions

    def to_text(self) -> str:
        self.compute_score()
        lines = []
        lines.append("=" * 56)
        lines.append("  PPT 质量检查报告")
        lines.append("=" * 56)
        lines.append(f"文件: {Path(self.file_path).name}")
        lines.append(f"页数: {self.slide_count}" +
                     (f" (期望 {self.expected_slides} 页)" if self.expected_slides else ""))
        lines.append(f"质量分数: {self.score:.0f}/100  {'✓ 通过' if self.passed else '✗ 未通过'}")
        lines.append("")

        if not self.issues:
            lines.append("✓ 未发现问题，PPT 质量良好！")
        else:
            for severity, label in [("error", "错误"), ("warning", "警告"), ("info", "提示")]:
                items = [i for i in self.issues if i.severity == severity]
                if items:
                    lines.append(f"【{label}】{len(items)}项")
                    for i, issue in enumerate(items, 1):
                        page = f"第{issue.slide_index+1}页" if issue.slide_index >= 0 else "全局"
                        lines.append(f"  {i}. [{page}] {issue.message}")
                        if issue.detail:
                            lines.append(f"     {issue.detail}")
                        if issue.fixable and issue.fix_action:
                            lines.append("     → 可自动修正")
                    lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        self.compute_score()
        return {
            "file_path": self.file_path,
            "slide_count": self.slide_count,
            "expected_slides": self.expected_slides,
            "score": self.score,
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
        }


# ==========================================
# PPTQualityChecker
# ==========================================

class PPTQualityChecker:
    """
    PPT 质量检查器

    用法:
        checker = PPTQualityChecker()

        # 检查文件
        report = checker.check("output.pptx", expected_slides=10)
        print(report.to_text())

        # 检查 Outline（生成前检查）
        report = checker.check_outline(outline, expected_slides=10)

        # 自动修正
        if not report.passed:
            fixed_outline = checker.fix_outline(outline, report)
    """

    # 字号阈值
    MIN_BODY_SIZE = 12       # pt
    MIN_TITLE_SIZE = 20
    MAX_TITLE_SIZE = 44
    RECOMMEND_BODY = 18
    RECOMMEND_TITLE = 28

    # 文字密度（每平方英寸字符数）
    MAX_CHARS_PER_SQ_INCH = 90

    # 每页最多要点数
    MAX_BULLETS = 8
    MIN_BULLETS = 2

    # 每页最多字符数
    MAX_CHARS_PER_SLIDE = 400

    def __init__(self, template_config=None):
        """
        Args:
            template_config: 可选的 TemplateConfig，用于检查模板遵循情况
        """
        self.template_config = template_config

    def check(self, file_path: str, expected_slides: int = None) -> PPTQualityReport:
        """
        检查已生成的 PPT 文件

        Args:
            file_path: .pptx 文件路径
            expected_slides: 期望页数（可选）
        """
        prs = Presentation(file_path)
        report = PPTQualityReport(
            file_path=file_path,
            slide_count=len(prs.slides),
            expected_slides=expected_slides,
        )

        slide_w = prs.slide_width / 914400
        slide_h = prs.slide_height / 914400

        all_fonts = []
        all_sizes = []

        for slide_idx, slide in enumerate(prs.slides):
            slide_text = ""
            slide_fonts = []
            has_content = False
            has_title = False
            shape_count = 0

            for shape in slide.shapes:
                shape_count += 1

                # 布局检查：元素是否超出页面边界
                self._check_shape_bounds(shape, slide_w, slide_h, slide_idx, report)

                if not shape.has_text_frame:
                    continue

                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        has_content = True
                        slide_text += text
                        # 第一行大字号视为标题
                        if not has_title and para.runs:
                            max_size = max(
                                (r.font.size.pt for r in para.runs if r.font.size),
                                default=0
                            )
                            if max_size >= self.MIN_TITLE_SIZE:
                                has_title = True

                    for run in para.runs:
                        if run.text.strip():
                            if run.font.name:
                                slide_fonts.append(run.font.name)
                                all_fonts.append(run.font.name)
                            if run.font.size:
                                all_sizes.append(run.font.size.pt)

                # 文字溢出检查
                self._check_text_overflow(shape, slide_idx, report)

            # 内容完整性检查
            self._check_content_completeness(
                slide_idx, has_content, has_title, slide_text,
                shape_count, report
            )

            # 字体一致性（单页）
            if len(set(slide_fonts)) > 2:
                report.issues.append(PPTQualityIssue(
                    slide_index=slide_idx,
                    issue_type="font",
                    severity=IssueSeverity.WARNING.value,
                    message=f"使用了{len(set(slide_fonts))}种字体",
                    detail=f"字体: {', '.join(list(set(slide_fonts))[:5])}",
                    fixable=True,
                    fix_action={"type": "unify_font", "slide": slide_idx},
                ))

        # 全局字体统一
        if len(set(all_fonts)) > 3:
            report.issues.append(PPTQualityIssue(
                slide_index=-1,
                issue_type="font",
                severity=IssueSeverity.WARNING.value,
                message=f"全文档使用了{len(set(all_fonts))}种字体",
                detail=f"建议统一为1-2种字体: {', '.join(list(set(all_fonts))[:5])}",
                fixable=True,
                fix_action={"type": "unify_font_all"},
            ))

        # 页数检查
        if expected_slides:
            self._check_slide_count(len(prs.slides), expected_slides, report)

        # 模板遵循检查
        if self.template_config:
            self._check_template_compliance(prs, report)

        report.compute_score()
        return report

    def check_outline(self, outline: PPTOutline,
                      expected_slides: int = None) -> PPTQualityReport:
        """
        生成前检查 PPTOutline（在生成 .pptx 之前发现问题）

        Args:
            outline: PPTOutline 对象
            expected_slides: 期望页数
        """
        report = PPTQualityReport(
            slide_count=len(outline.slides),
            expected_slides=expected_slides,
        )

        for idx, slide in enumerate(outline.slides):
            # 标题检查
            if not slide.title and slide.layout not in ("cover", "quote", "summary", "blank"):
                report.issues.append(PPTQualityIssue(
                    slide_index=idx,
                    issue_type="content",
                    severity=IssueSeverity.ERROR.value,
                    message="缺少标题",
                    detail="非封面/引用/总结页应有标题",
                    fixable=True,
                    fix_action={"type": "add_title", "slide": idx},
                ))

            # 要点数量检查
            bullets = slide.bullets or []
            if slide.layout == "content" and len(bullets) > self.MAX_BULLETS:
                report.issues.append(PPTQualityIssue(
                    slide_index=idx,
                    issue_type="overflow",
                    severity=IssueSeverity.WARNING.value,
                    message=f"要点过多（{len(bullets)}条）",
                    detail=f"建议每页不超过{self.MAX_BULLETS}条，考虑分页或精简",
                    fixable=True,
                    fix_action={"type": "reduce_bullets", "slide": idx, "max": self.MAX_BULLETS},
                ))

            if slide.layout in ("content", "content_list") and len(bullets) < self.MIN_BULLETS \
                    and not slide.body_text and not slide.data:
                report.issues.append(PPTQualityIssue(
                    slide_index=idx,
                    issue_type="content",
                    severity=IssueSeverity.INFO.value,
                    message=f"内容较少（{len(bullets)}条要点）",
                    detail="建议补充更多内容",
                    fixable=False,
                ))

            # 文字量估算
            total_chars = len(slide.title or "")
            total_chars += sum(len(b) if isinstance(b, str) else len(str(b)) for b in bullets)
            total_chars += len(slide.body_text or "")
            if total_chars > self.MAX_CHARS_PER_SLIDE:
                report.issues.append(PPTQualityIssue(
                    slide_index=idx,
                    issue_type="overflow",
                    severity=IssueSeverity.WARNING.value,
                    message=f"文字量过大（约{total_chars}字）",
                    detail=f"建议每页不超过{self.MAX_CHARS_PER_SLIDE}字",
                    fixable=True,
                    fix_action={"type": "reduce_text", "slide": idx},
                ))

            # 表格数据检查
            if slide.layout == "table" and not slide.table_data:
                report.issues.append(PPTQualityIssue(
                    slide_index=idx,
                    issue_type="content",
                    severity=IssueSeverity.ERROR.value,
                    message="表格页缺少表格数据",
                    fixable=False,
                ))

            # 图表数据检查
            if slide.layout == "chart" and (not slide.chart_categories or not slide.chart_series):
                report.issues.append(PPTQualityIssue(
                    slide_index=idx,
                    issue_type="content",
                    severity=IssueSeverity.ERROR.value,
                    message="图表页缺少图表数据",
                    detail="需要 chart_categories 和 chart_series",
                    fixable=False,
                ))

        # 页数检查
        if expected_slides:
            self._check_slide_count(len(outline.slides), expected_slides, report)

        # 封面和总结检查
        layouts = [s.layout for s in outline.slides]
        if layouts and layouts[0] != "cover":
            report.issues.append(PPTQualityIssue(
                slide_index=0,
                issue_type="layout",
                severity=IssueSeverity.WARNING.value,
                message="建议第一页为封面页",
                fixable=True,
                fix_action={"type": "insert_cover"},
            ))
        if layouts and layouts[-1] not in ("summary", "thankyou"):
            report.issues.append(PPTQualityIssue(
                slide_index=len(layouts) - 1,
                issue_type="layout",
                severity=IssueSeverity.INFO.value,
                message="建议最后一页为总结/感谢页",
                fixable=True,
                fix_action={"type": "append_summary"},
            ))

        report.compute_score()
        return report

    def fix_outline(self, outline: PPTOutline, report: PPTQualityReport) -> PPTOutline:
        """
        自动修正 Outline 中的问题

        Args:
            outline: 原始 PPTOutline
            report: 质量报告

        Returns:
            修正后的 PPTOutline
        """
        fixed = copy.deepcopy(outline)
        actions = report.get_fix_actions()

        for action in actions:
            atype = action.get("type")

            if atype == "reduce_bullets":
                slide_idx = action["slide"]
                max_bullets = action.get("max", self.MAX_BULLETS)
                if slide_idx < len(fixed.slides):
                    slide = fixed.slides[slide_idx]
                    if len(slide.bullets) > max_bullets:
                        overflow = slide.bullets[max_bullets:]
                        slide.bullets = slide.bullets[:max_bullets]
                        continuation = copy.deepcopy(slide)
                        continuation.title = f"{slide.title}（续）"
                        continuation.bullets = overflow
                        fixed.slides.insert(slide_idx + 1, continuation)
                        self._renumber(fixed)

            elif atype == "reduce_text":
                slide_idx = action["slide"]
                if slide_idx < len(fixed.slides):
                    slide = fixed.slides[slide_idx]
                    slide.body_font_size = max(12, (slide.body_font_size or 18) - 2)
                    slide.notes = (slide.notes or "") + "\n已为高密度内容降低字号，正文未截断。"

            elif atype == "remove_slides":
                # 页数超预算：从"总结页之前"裁掉多余页并重新编号
                try:
                    count = int(action.get("count", 0))
                except (TypeError, ValueError):
                    count = 0
                if count > 0 and len(fixed.slides) > 2:
                    removable = fixed.slides[1:-1]
                    del removable[:count]
                    fixed.slides = [fixed.slides[0]] + removable + [fixed.slides[-1]]
                    for i, s in enumerate(fixed.slides):
                        s.page_number = i + 1
                    fixed.changes = getattr(fixed, "changes", [])
                    fixed.changes.append(f"已按页数要求裁剪 {count} 页")

            elif atype == "add_slides":
                # 不凭空生成内容；该问题在检查阶段标为不可自动修复。
                continue

            elif atype == "unify_font":
                slide_idx = action.get("slide", -1)
                if 0 <= slide_idx < len(fixed.slides):
                    fixed.slides[slide_idx].notes = (
                        (fixed.slides[slide_idx].notes or "")
                        + "\n生成时统一使用大纲字体方案。"
                    )

            elif atype == "unify_font_all":
                for slide in fixed.slides:
                    slide.notes = (slide.notes or "") + "\n生成时统一使用大纲字体方案。"

            elif atype == "reduce_font":
                slide_idx = action.get("slide", -1)
                if 0 <= slide_idx < len(fixed.slides):
                    slide = fixed.slides[slide_idx]
                    slide.body_font_size = max(12, (slide.body_font_size or 18) - 2)

            elif atype == "insert_cover":
                # 在开头插入封面
                if fixed.slides and fixed.slides[0].layout != "cover":
                    cover = SlideContent(
                        layout="cover",
                        title=fixed.title or "演示文稿",
                        subtitle=fixed.subtitle or "",
                        page_number=1,
                    )
                    fixed.slides.insert(0, cover)
                    self._renumber(fixed)

            elif atype == "append_summary":
                # 在末尾添加总结页
                if fixed.slides and fixed.slides[-1].layout != "summary":
                    summary = SlideContent(
                        layout="summary",
                        title="感谢聆听",
                        page_number=len(fixed.slides) + 1,
                    )
                    fixed.slides.append(summary)
                    self._renumber(fixed)

            elif atype == "add_title":
                slide_idx = action["slide"]
                if slide_idx < len(fixed.slides):
                    slide = fixed.slides[slide_idx]
                    if not slide.title:
                        slide.title = f"第{slide_idx + 1}页"

            elif atype == "apply_template_size":
                # 页面尺寸对齐模板：outline 层即可修正，生成时按此尺寸渲染
                if self.template_config:
                    fixed.slide_width = self.template_config.slide_width
                    fixed.slide_height = self.template_config.slide_height

        return fixed

    @staticmethod
    def _renumber(outline: PPTOutline):
        """重新编号"""
        for i, slide in enumerate(outline.slides, 1):
            slide.page_number = i

    # ==========================================
    # 各项检查实现
    # ==========================================

    def _check_shape_bounds(self, shape, slide_w: float, slide_h: float,
                            slide_idx: int, report: PPTQualityReport):
        """检查元素是否超出页面边界"""
        try:
            left = shape.left / 914400 if shape.left else 0
            top = shape.top / 914400 if shape.top else 0
            width = shape.width / 914400 if shape.width else 0
            height = shape.height / 914400 if shape.height else 0

            margin = 0.1  # 允许 0.1 英寸误差

            if left < -margin or top < -margin:
                report.issues.append(PPTQualityIssue(
                    slide_index=slide_idx,
                    issue_type="layout",
                    severity=IssueSeverity.WARNING.value,
                    message="元素超出页面左/上边界",
                    detail=f"位置: ({left:.1f}, {top:.1f})",
                    fixable=False,
                ))
            elif left + width > slide_w + margin or top + height > slide_h + margin:
                report.issues.append(PPTQualityIssue(
                    slide_index=slide_idx,
                    issue_type="layout",
                    severity=IssueSeverity.WARNING.value,
                    message="元素超出页面右/下边界",
                    detail=f"元素: ({left:.1f},{top:.1f}) 大小: {width:.1f}x{height:.1f}, "
                           f"页面: {slide_w:.1f}x{slide_h:.1f}",
                    fixable=False,
                ))
        except (AttributeError, TypeError, ValueError):
            # python-pptx 异形元素坐标不可读时跳过该元素，但必须留痕；
            # KeyError/NameError 等编程错误不再被静默吞掉。
            logger.debug(
                f"元素边界检查跳过（坐标不可读）: slide={slide_idx}",
                exc_info=True,
            )

    def _check_text_overflow(self, shape, slide_idx: int, report: PPTQualityReport):
        """检查文字是否溢出文本框（估算）"""
        if not shape.has_text_frame:
            return

        try:
            tf = shape.text_frame
            if not tf.text.strip():
                return

            width = shape.width / 914400 if shape.width else 10
            height = shape.height / 914400 if shape.height else 1

            # 估算每行可容纳字符数（中文约占字号宽度，英文约一半）
            total_lines = 0
            for para in tf.paragraphs:
                text = para.text
                if not text.strip():
                    total_lines += 0.5
                    continue

                # 获取该段落字号
                font_size = self.RECOMMEND_BODY
                for run in para.runs:
                    if run.font.size:
                        font_size = run.font.size.pt
                        break

                # 估算每行字符数（英寸 * 72 / 字号 * 密度系数）
                chars_per_line = max(1, int((width * 72) / (font_size * 0.9)))
                # 中文字符占2个单位
                effective_len = sum(2 if ord(c) > 127 else 1 for c in text)
                effective_cpl = max(1, int(chars_per_line * 1.5))
                lines_needed = max(1, math.ceil(effective_len / effective_cpl))
                total_lines += lines_needed

            # 估算文本框可容纳行数
            if total_lines > 0:
                # 获取平均字号
                avg_size = self.RECOMMEND_BODY
                sizes = []
                for para in tf.paragraphs:
                    for run in para.runs:
                        if run.font.size:
                            sizes.append(run.font.size.pt)
                if sizes:
                    avg_size = sum(sizes) / len(sizes)

                line_height = avg_size * 1.3 / 72  # 行距约1.3倍
                max_lines = max(1, int(height / line_height))

                if total_lines > max_lines + 1:
                    report.issues.append(PPTQualityIssue(
                        slide_index=slide_idx,
                        issue_type="overflow",
                        severity=IssueSeverity.WARNING.value,
                        message="文字可能溢出文本框",
                        detail=f"约需{total_lines}行，文本框约容纳{max_lines}行",
                        fixable=True,
                        fix_action={"type": "reduce_font", "slide": slide_idx},
                    ))
        except (AttributeError, TypeError, ValueError):
            # python-pptx 异形文本框不可估算时跳过，但必须留痕；
            # KeyError/NameError 等编程错误不再被静默吞掉。
            logger.debug(
                f"文字溢出估算跳过（文本框不可读）: slide={slide_idx}",
                exc_info=True,
            )

    def _check_content_completeness(self, slide_idx: int, has_content: bool,
                                     has_title: bool, slide_text: str,
                                     shape_count: int, report: PPTQualityReport):
        """检查内容完整性"""
        # 空白页（非首尾）
        if not has_content and shape_count <= 1 and slide_idx > 0:
            report.issues.append(PPTQualityIssue(
                slide_index=slide_idx,
                issue_type="content",
                severity=IssueSeverity.WARNING.value,
                message="页面内容为空或极少",
                fixable=False,
            ))

    def _check_slide_count(self, actual: int, expected: int, report: PPTQualityReport):
        """检查页数是否符合要求"""
        if actual == expected:
            return

        diff = actual - expected
        severity = (IssueSeverity.WARNING.value if abs(diff) <= 2
                    else IssueSeverity.ERROR.value)

        if diff > 0:
            message = f"页数超出要求（实际{actual}页，期望{expected}页，多{diff}页）"
            action = {"type": "remove_slides", "count": diff}
        else:
            message = f"页数不足（实际{actual}页，期望{expected}页，少{-diff}页）"
            action = {"type": "add_slides", "count": -diff}

        report.issues.append(PPTQualityIssue(
            slide_index=-1,
            issue_type="count",
            severity=severity,
            message=message,
            fixable=(diff > 0 and abs(diff) <= 3),
            fix_action=action if diff > 0 and abs(diff) <= 3 else {},
        ))

    def _check_template_compliance(self, prs, report: PPTQualityReport):
        """检查是否遵循模板配置"""
        if not self.template_config:
            return

        config = self.template_config

        # 检查页面尺寸
        actual_w = prs.slide_width / 914400
        actual_h = prs.slide_height / 914400
        if abs(actual_w - config.slide_width) > 0.1 or abs(actual_h - config.slide_height) > 0.1:
            report.issues.append(PPTQualityIssue(
                slide_index=-1,
                issue_type="template",
                severity=IssueSeverity.WARNING.value,
                message="页面尺寸与模板不一致",
                detail=f"模板: {config.slide_width:.1f}x{config.slide_height:.1f}, "
                       f"实际: {actual_w:.1f}x{actual_h:.1f}",
                fixable=True,
                fix_action={"type": "apply_template_size"},
            ))


# ==========================================
# 便捷函数
# ==========================================

def check_ppt(file_path: str, expected_slides: int = None) -> PPTQualityReport:
    """便捷函数：检查 PPT 质量"""
    return PPTQualityChecker().check(file_path, expected_slides)


def check_and_fix_outline(outline: PPTOutline,
                          expected_slides: int = None) -> Tuple[PPTOutline, PPTQualityReport]:
    """
    检查并自动修正 Outline

    Returns:
        (修正后的 outline, 质量报告)
    """
    checker = PPTQualityChecker()
    report = checker.check_outline(outline, expected_slides)
    fixed = checker.fix_outline(outline, report)
    return fixed, report
