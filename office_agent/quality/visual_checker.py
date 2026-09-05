"""
Word Visual Checker - Word 文档视觉质量检查器

将 docx 渲染为图片，通过视觉模型检查：
- 标题是否突出
- 段落是否拥挤
- 分页是否合理
- 表格是否美观
- 空白是否合理

输出视觉优化建议。
"""
import os
import json
import logging
import re
import tempfile
import subprocess
import shutil
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict
from enum import Enum

from ..vision_gateway import (
    VisionGateway, VisionRequest,
    ImageInput, DocumentPage,
    VisionTaskType,
)

logger = logging.getLogger(__name__)


class VisualCategory(Enum):
    """视觉检查类别"""
    HEADING = "heading"           # 标题突出度
    PARAGRAPH = "paragraph"       # 段落间距/拥挤度
    PAGE_BREAK = "page_break"     # 分页合理性
    TABLE = "table"               # 表格美观度
    WHITESPACE = "whitespace"     # 空白/留白
    ALIGNMENT = "alignment"       # 对齐
    CONSISTENCY = "consistency"   # 一致性
    OVERALL = "overall"           # 整体观感


class VisualSeverity(Enum):
    """视觉问题严重程度"""
    GOOD = "good"         # 良好
    MINOR = "minor"       # 轻微问题
    MODERATE = "moderate" # 中等问题
    MAJOR = "major"       # 严重问题


@dataclass
class VisualIssue:
    """视觉问题"""
    category: str = ""           # VisualCategory 值
    severity: str = ""           # VisualSeverity 值
    page: int = 0                # 所在页码
    description: str = ""        # 问题描述
    suggestion: str = ""         # 优化建议
    location: str = ""           # 位置描述（如"第1页顶部"）
    confidence: float = 0.0      # 置信度

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PageVisualResult:
    """单页视觉检查结果"""
    page_number: int = 1
    issues: List[VisualIssue] = field(default_factory=list)
    score: float = 100.0         # 单页评分
    summary: str = ""            # 单页评价
    raw_response: str = ""       # 原始模型响应
    score_details: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "page_number": self.page_number,
            "score": self.score,
            "summary": self.summary,
            "score_details": self.score_details,
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass
class VisualCheckReport:
    """视觉检查报告"""
    file_path: str = ""
    total_pages: int = 0
    overall_score: float = 100.0
    passed: bool = True
    pages: List[PageVisualResult] = field(default_factory=list)
    all_issues: List[VisualIssue] = field(default_factory=list)
    summary: str = ""
    top_suggestions: List[str] = field(default_factory=list)
    category_scores: Dict[str, float] = field(default_factory=dict)
    error: str = ""

    @property
    def success(self) -> bool:
        return self.error == ""

    def get_issues_by_category(self, category: str) -> List[VisualIssue]:
        return [i for i in self.all_issues if i.category == category]

    def get_issues_by_severity(self, severity: str) -> List[VisualIssue]:
        return [i for i in self.all_issues if i.severity == severity]

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "total_pages": self.total_pages,
            "overall_score": self.overall_score,
            "passed": self.passed,
            "summary": self.summary,
            "top_suggestions": self.top_suggestions,
            "category_scores": self.category_scores,
            "pages": [p.to_dict() for p in self.pages],
            "all_issues": [i.to_dict() for i in self.all_issues],
        }

    def to_text(self) -> str:
        """生成可读的文本报告"""
        lines = []
        lines.append("=" * 60)
        lines.append("  Word 文档视觉检查报告")
        lines.append("=" * 60)
        lines.append(f"文件: {self.file_path}")
        lines.append(f"页数: {self.total_pages}")
        lines.append(f"总体评分: {self.overall_score:.1f}/100")
        lines.append(f"结论: {'通过' if self.passed else '需要优化'}")
        lines.append("")

        if self.summary:
            lines.append("【总体评价】")
            lines.append(f"  {self.summary}")
            lines.append("")

        if self.category_scores:
            lines.append("【分类评分】")
            cat_names = {
                "heading": "标题突出度",
                "paragraph": "段落间距",
                "page_break": "分页合理性",
                "table": "表格美观度",
                "whitespace": "留白合理性",
                "alignment": "对齐",
                "consistency": "一致性",
                "overall": "整体观感",
            }
            for cat, score in self.category_scores.items():
                name = cat_names.get(cat, cat)
                bar = "█" * int(score / 5) + "░" * (20 - int(score / 5))
                lines.append(f"  {name}: {bar} {score:.0f}")
            lines.append("")

        if self.top_suggestions:
            lines.append("【优化建议】")
            for i, s in enumerate(self.top_suggestions[:10], 1):
                lines.append(f"  {i}. {s}")
            lines.append("")

        # 按页列出问题
        major_issues = self.get_issues_by_severity("major")
        moderate_issues = self.get_issues_by_severity("moderate")
        minor_issues = self.get_issues_by_severity("minor")

        if major_issues:
            lines.append(f"【严重问题】({len(major_issues)}项)")
            for iss in major_issues:
                loc = f"第{iss.page}页" if iss.page else ""
                lines.append(f"  ✗ [{loc}] {iss.description}")
                if iss.suggestion:
                    lines.append(f"    → {iss.suggestion}")
            lines.append("")

        if moderate_issues:
            lines.append(f"【中等问题】({len(moderate_issues)}项)")
            for iss in moderate_issues:
                loc = f"第{iss.page}页" if iss.page else ""
                lines.append(f"  △ [{loc}] {iss.description}")
                if iss.suggestion:
                    lines.append(f"    → {iss.suggestion}")
            lines.append("")

        if minor_issues:
            lines.append(f"【轻微问题】({len(minor_issues)}项)")
            for iss in minor_issues[:10]:
                loc = f"第{iss.page}页" if iss.page else ""
                lines.append(f"  ○ [{loc}] {iss.description}")
            if len(minor_issues) > 10:
                lines.append(f"  ... 还有 {len(minor_issues) - 10} 项")
            lines.append("")

        return "\n".join(lines)


class WordVisualChecker:
    """
    Word 文档视觉检查器

    使用方式:
        checker = WordVisualChecker(vision_gateway)
        report = checker.check("document.docx")
        print(report.to_text())
    """

    # 系统提示词
    SYSTEM_PROMPT = """你是一个专业的文档排版视觉评审专家。你需要从视觉角度评审Word文档的排版质量。

请从以下维度逐一检查每一页：

1. **标题突出度** (heading)
   - 一级标题是否足够大、加粗、与正文有明显区分
   - 标题前后间距是否合适
   - 标题层级是否清晰（一/二/三级标题有视觉区分）

2. **段落间距** (paragraph)
   - 段落之间是否有适当间距
   - 行间距是否舒适（不要太挤也不要太松）
   - 是否有大段文字堆砌造成拥挤感
   - 首行缩进是否统一

3. **分页合理性** (page_break)
   - 是否有标题孤立在页底（标题后无正文）
   - 表格是否被不合理地截断
   - 段落是否在不恰当的位置被分页
   - 页面内容分布是否均匀

4. **表格美观度** (table)
   - 表格线条是否简洁规范
   - 表头是否突出
   - 单元格内容是否对齐
   - 表格宽度是否合适

5. **留白合理性** (whitespace)
   - 页边距是否合适
   - 是否有大面积空白
   - 内容是否过于靠边
   - 图文之间留白是否协调

6. **对齐** (alignment)
   - 正文是否两端对齐或左对齐统一
   - 是否有参差不齐的情况
   - 居中/右对齐使用是否恰当

7. **一致性** (consistency)
   - 同级标题样式是否一致
   - 正文字体字号是否统一
   - 段落格式是否统一

请以JSON格式返回检查结果，格式如下：
```json
{
  "page_summary": "本页整体评价（一句话）",
  "page_score": 85,
  "category_scores": {
    "heading": 90,
    "paragraph": 80,
    "page_break": 85,
    "table": 90,
    "whitespace": 75,
    "alignment": 90,
    "consistency": 85,
    "overall": 85
  },
  "issues": [
    {
      "category": "paragraph",
      "severity": "moderate",
      "description": "第二段文字行间距过小，显得拥挤",
      "suggestion": "将行间距调整为1.5倍行距",
      "location": "页面中部",
      "confidence": 0.9
    }
  ]
}
```

severity 取值：good(无问题)、minor(轻微)、moderate(中等)、major(严重)
category 取值：heading, paragraph, page_break, table, whitespace, alignment, consistency, overall
只返回JSON，不要其他解释。如果某方面没有问题，category_scores中给高分，issues中不包含该项。"""

    def __init__(self, vision_gateway: Optional[VisionGateway] = None,
                 dpi: int = 150, max_pages: int = 30):
        """
        Args:
            vision_gateway: VisionGateway 实例（如果为None则需要后续设置）
            dpi: 渲染 DPI
            max_pages: 最大检查页数
        """
        self.gateway = vision_gateway
        self.dpi = dpi
        self.max_pages = max_pages
        self._temp_dir = tempfile.mkdtemp(prefix="word_visual_")

    def set_gateway(self, gateway: VisionGateway):
        """设置视觉网关"""
        self.gateway = gateway

    def check(self, docx_path: str,
              model_key: Optional[str] = None,
              dpi: Optional[int] = None) -> VisualCheckReport:
        """
        检查 Word 文档视觉质量

        Args:
            docx_path: docx 文件路径
            model_key: 指定视觉模型
            dpi: 渲染 DPI（覆盖默认值）

        Returns:
            VisualCheckReport
        """
        from ..vision_gateway.document_renderer import DocumentRenderer

        report = VisualCheckReport(file_path=docx_path)

        if not self.gateway:
            report.error = "未配置 VisionGateway，请先设置视觉模型"
            return report

        if not os.path.exists(docx_path):
            report.error = f"文件不存在: {docx_path}"
            return report

        render_dpi = dpi or self.dpi

        try:
            # Step 1: docx → PDF
            pdf_path = self._docx_to_pdf(docx_path)
            if not pdf_path or not os.path.exists(pdf_path):
                report.error = (
                    "无法将 docx 转为 PDF。请确保已安装 LibreOffice，"
                    "或使用 check_from_pdf 直接检查 PDF 文件。"
                )
                return report

            # Step 2: PDF → 图片
            renderer = DocumentRenderer(
                dpi=render_dpi,
                max_pages=self.max_pages,
                output_dir=self._temp_dir,
            )
            pages = renderer.render(pdf_path)

            if not pages:
                report.error = "文档渲染失败，没有可检查的页面"
                return report

            report.total_pages = len(pages)

            # Step 3: 逐页视觉检查
            all_category_scores: Dict[str, List[float]] = {}
            all_issues = []

            for page in pages:
                if not page.image:
                    continue

                page_result = self._check_page(page, model_key)
                report.pages.append(page_result)
                all_issues.extend(page_result.issues)

                # 收集分类评分
                for cat, score in page_result.score_details.items():
                    if cat not in all_category_scores:
                        all_category_scores[cat] = []
                    all_category_scores[cat].append(score)

            report.all_issues = all_issues

            # Step 4: 计算总体评分
            if report.pages:
                report.overall_score = sum(p.score for p in report.pages) / len(report.pages)

            # 分类平均评分
            for cat, scores in all_category_scores.items():
                report.category_scores[cat] = sum(scores) / len(scores)

            # Step 5: 生成总结和建议
            report.summary = self._generate_summary(report)
            report.top_suggestions = self._generate_top_suggestions(report)

            # 是否通过（没有 major 问题且分数 >= 70）
            major_count = len(report.get_issues_by_severity("major"))
            report.passed = major_count == 0 and report.overall_score >= 70

        except Exception as e:
            report.error = f"视觉检查失败: {e}"

        return report

    def check_from_pdf(self, pdf_path: str,
                       model_key: Optional[str] = None,
                       dpi: Optional[int] = None) -> VisualCheckReport:
        """
        直接从 PDF 检查（如果已有 PDF）

        Args:
            pdf_path: PDF 文件路径
            model_key: 指定视觉模型
            dpi: 渲染 DPI
        """
        from ..vision_gateway.document_renderer import DocumentRenderer

        report = VisualCheckReport(file_path=pdf_path)

        if not self.gateway:
            report.error = "未配置 VisionGateway"
            return report

        render_dpi = dpi or self.dpi

        try:
            renderer = DocumentRenderer(
                dpi=render_dpi,
                max_pages=self.max_pages,
                output_dir=self._temp_dir,
            )
            pages = renderer.render(pdf_path)

            if not pages:
                report.error = "PDF 渲染失败"
                return report

            report.total_pages = len(pages)

            all_category_scores: Dict[str, List[float]] = {}
            all_issues = []

            for page in pages:
                if not page.image:
                    continue
                page_result = self._check_page(page, model_key)
                report.pages.append(page_result)
                all_issues.extend(page_result.issues)
                for cat, score in page_result.score_details.items():
                    if cat not in all_category_scores:
                        all_category_scores[cat] = []
                    all_category_scores[cat].append(score)

            report.all_issues = all_issues

            if report.pages:
                report.overall_score = sum(p.score for p in report.pages) / len(report.pages)

            for cat, scores in all_category_scores.items():
                report.category_scores[cat] = sum(scores) / len(scores)

            report.summary = self._generate_summary(report)
            report.top_suggestions = self._generate_top_suggestions(report)

            major_count = len(report.get_issues_by_severity("major"))
            report.passed = major_count == 0 and report.overall_score >= 70

        except Exception as e:
            report.error = f"视觉检查失败: {e}"

        return report

    def check_images(self, image_paths: List[str],
                     model_key: Optional[str] = None) -> VisualCheckReport:
        """
        直接从图片列表检查

        Args:
            image_paths: 图片路径列表（按页顺序）
            model_key: 指定视觉模型
        """
        report = VisualCheckReport(file_path="(images)")
        report.total_pages = len(image_paths)

        if not self.gateway:
            report.error = "未配置 VisionGateway"
            return report

        all_category_scores: Dict[str, List[float]] = {}
        all_issues = []

        for i, img_path in enumerate(image_paths):
            page = DocumentPage(
                page_number=i + 1,
                image=ImageInput.from_file(img_path, page_number=i + 1),
            )
            page_result = self._check_page(page, model_key)
            report.pages.append(page_result)
            all_issues.extend(page_result.issues)
            for cat, score in page_result.score_details.items():
                if cat not in all_category_scores:
                    all_category_scores[cat] = []
                all_category_scores[cat].append(score)

        report.all_issues = all_issues

        if report.pages:
            report.overall_score = sum(p.score for p in report.pages) / len(report.pages)

        for cat, scores in all_category_scores.items():
            report.category_scores[cat] = sum(scores) / len(scores)

        report.summary = self._generate_summary(report)
        report.top_suggestions = self._generate_top_suggestions(report)

        major_count = len(report.get_issues_by_severity("major"))
        report.passed = major_count == 0 and report.overall_score >= 70

        return report

    def _check_page(self, page: DocumentPage,
                    model_key: Optional[str] = None) -> PageVisualResult:
        """检查单页"""
        result = PageVisualResult(page_number=page.page_number)

        image = page.image
        gateway = self.gateway
        if image is None or gateway is None:
            result.summary = "缺少图像或未配置 VisionGateway"
            result.score = 0
            return result

        request = VisionRequest(
            images=[image],
            prompt=self._build_page_prompt(page),
            task_type=VisionTaskType.QUALITY_CHECK,
            system_prompt=self.SYSTEM_PROMPT,
            require_structured=True,
            temperature=0.2,
        )

        resp = gateway.analyze(request, model_key=model_key)

        if not resp.success:
            result.summary = f"检查失败: {resp.error}"
            result.score = 0
            return result

        result.raw_response = resp.content

        # 解析结果
        parsed = self._parse_page_response(resp.content, page.page_number)
        result.summary = parsed.get("page_summary", "")
        result.score = float(parsed.get("page_score", 70))
        result.score_details = parsed.get("category_scores", {})

        for iss_data in parsed.get("issues", []):
            issue = VisualIssue(
                category=iss_data.get("category", "overall"),
                severity=iss_data.get("severity", "minor"),
                page=page.page_number,
                description=iss_data.get("description", ""),
                suggestion=iss_data.get("suggestion", ""),
                location=iss_data.get("location", ""),
                confidence=float(iss_data.get("confidence", 0.5)),
            )
            # 只添加非 good 的问题
            if issue.severity != "good" and issue.description:
                result.issues.append(issue)

        return result

    def _build_page_prompt(self, page: DocumentPage) -> str:
        """构建单页检查提示"""
        prompt = f"请检查这是文档的第 {page.page_number} 页。"
        if page.text_hint:
            prompt += f"\n\n页面文字内容（供参考）：\n{page.text_hint[:300]}"
        prompt += "\n\n请从视觉排版角度全面检查这一页，返回JSON格式的检查结果。"
        return prompt

    def _parse_page_response(self, text: str, page_num: int) -> dict:
        """解析模型返回的 JSON"""
        # 提取 JSON
        json_str = self._extract_json(text)
        if not json_str:
            return {"page_summary": text[:200], "page_score": 70, "issues": []}

        try:
            data = json.loads(json_str, strict=False)
            return data
        except json.JSONDecodeError:
            return {"page_summary": "解析失败", "page_score": 70, "issues": []}

    @staticmethod
    def _extract_json(text: str) -> str:
        """从文本中提取 JSON"""
        text = re.sub(r'```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```', '', text)

        start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                start = i
                break
        if start < 0:
            return ""

        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
        return ""

    def _docx_to_pdf(self, docx_path: str) -> Optional[str]:
        """将 docx 转为 PDF"""
        # 查找 LibreOffice
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            win_paths = [
                r"C:\Program Files\LibreOffice\program\soffice.exe",
                r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            ]
            for p in win_paths:
                if os.path.exists(p):
                    soffice = p
                    break

        if not soffice:
            return None

        out_pdf = os.path.join(
            self._temp_dir,
            Path(docx_path).stem + ".pdf"
        )
        # 独立 UserInstallation profile：即使超时后整树强杀残留了 soffice
        # 进程，也不会锁住共享默认 profile 导致后续所有转换失败。
        profile_dir = tempfile.mkdtemp(prefix="lo-profile-")
        cmd = [
            soffice, "--headless",
            f"-env:UserInstallation={Path(profile_dir).resolve().as_uri()}",
            "--convert-to", "pdf",
            "--outdir", self._temp_dir,
            docx_path
        ]
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
            )
            proc.communicate(timeout=120)
            if proc.returncode == 0 and os.path.exists(out_pdf):
                return out_pdf
            logger.warning(
                "LibreOffice docx 转换失败（returncode=%s），降级为非视觉检查",
                proc.returncode,
            )
        except subprocess.TimeoutExpired:
            # Windows 上 soffice.exe 只是启动器，真实进程是 soffice.bin；
            # subprocess 的 timeout kill 只杀直接子进程，必须整树终止并
            # reap，否则孤儿进程常驻且持有 profile 锁。
            if proc is not None:
                self._kill_process_tree(proc)
                try:
                    proc.communicate(timeout=10)
                except Exception:
                    pass
            logger.warning("LibreOffice docx 转换超时，已终止进程树，降级为非视觉检查")
        except Exception:
            logger.warning("LibreOffice docx 转换异常，降级为非视觉检查", exc_info=True)
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)

        return None

    @staticmethod
    def _kill_process_tree(proc) -> None:
        """终止进程及其整棵子进程树（Windows 上 soffice.exe 只是启动器）。"""
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                )
            else:
                proc.kill()
        except Exception:
            logger.warning("终止 LibreOffice 进程树失败", exc_info=True)

    def _generate_summary(self, report: VisualCheckReport) -> str:
        """生成总体评价"""
        score = report.overall_score
        major = len(report.get_issues_by_severity("major"))
        moderate = len(report.get_issues_by_severity("moderate"))
        minor = len(report.get_issues_by_severity("minor"))

        if score >= 90:
            base = "文档整体排版质量优秀，视觉效果良好。"
        elif score >= 80:
            base = "文档整体排版质量较好，存在少量可优化之处。"
        elif score >= 70:
            base = "文档排版基本合格，但有一些需要改进的地方。"
        elif score >= 60:
            base = "文档排版存在较多问题，建议进行系统性优化。"
        else:
            base = "文档排版质量较差，需要重新调整排版。"

        parts = [base]
        if major:
            parts.append(f"发现 {major} 项严重问题需要立即修复。")
        if moderate:
            parts.append(f"{moderate} 项中等问题建议改进。")
        if minor and not major and not moderate:
            parts.append(f"{minor} 项轻微问题可酌情优化。")

        return " ".join(parts)

    def _generate_top_suggestions(self, report: VisualCheckReport) -> List[str]:
        """生成最重要的优化建议"""
        suggestions = []

        # 按严重程度排序
        severity_order = {"major": 0, "moderate": 1, "minor": 2}
        sorted_issues = sorted(
            report.all_issues,
            key=lambda x: severity_order.get(x.severity, 3)
        )

        seen = set()
        for iss in sorted_issues:
            if iss.suggestion and iss.suggestion not in seen:
                loc = f"（第{iss.page}页）" if iss.page else ""
                suggestions.append(f"{iss.suggestion}{loc}")
                seen.add(iss.suggestion)
                if len(suggestions) >= 10:
                    break

        return suggestions

    def cleanup(self):
        """清理临时文件"""
        import shutil as sh
        sh.rmtree(self._temp_dir, ignore_errors=True)

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass


def check_word_visual(docx_path: str,
                      gateway: Optional[VisionGateway] = None,
                      model_key: Optional[str] = None,
                      dpi: int = 150) -> VisualCheckReport:
    """
    便捷函数：检查 Word 文档视觉质量

    Args:
        docx_path: docx 文件路径
        gateway: VisionGateway 实例
        model_key: 指定模型
        dpi: 渲染 DPI

    Returns:
        VisualCheckReport
    """
    checker = WordVisualChecker(vision_gateway=gateway, dpi=dpi)
    return checker.check(docx_path, model_key=model_key)
