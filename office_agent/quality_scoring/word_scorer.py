"""
Word Quality Scorer - Word文档质量评分引擎

评分维度：
1. 格式正确率 (format_accuracy): 字体、字号、对齐、行距、缩进等格式是否正确
2. 标题识别率 (heading_recognition): 标题层级是否正确识别和应用
3. 排版一致性 (layout_consistency): 同类元素格式是否一致
"""
import os
import re
from collections import Counter
from typing import Dict, List, Any
from dataclasses import dataclass, field

from docx import Document
from docx.document import Document as DocumentType


@dataclass
class WordScoreResult:
    """Word评分结果"""
    file_path: str = ""
    total_score: float = 0.0

    # 各维度分数 (0-100)
    format_accuracy: float = 0.0
    heading_recognition: float = 0.0
    layout_consistency: float = 0.0

    # 详细信息
    format_details: Dict[str, Any] = field(default_factory=dict)
    heading_details: Dict[str, Any] = field(default_factory=dict)
    layout_details: Dict[str, Any] = field(default_factory=dict)

    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "total_score": round(self.total_score, 1),
            "scores": {
                "format_accuracy": round(self.format_accuracy, 1),
                "heading_recognition": round(self.heading_recognition, 1),
                "layout_consistency": round(self.layout_consistency, 1),
            },
            "details": {
                "format": self.format_details,
                "heading": self.heading_details,
                "layout": self.layout_details,
            },
            "issues": self.issues,
            "suggestions": self.suggestions,
        }


class WordQualityScorer:
    """Word文档质量评分器"""

    # 总分权重（三者之和必须为 1.0）：格式准确性 > 标题识别 > 排版一致性
    WEIGHT_FORMAT_ACCURACY = 0.4
    WEIGHT_HEADING_RECOGNITION = 0.35
    WEIGHT_LAYOUT_CONSISTENCY = 0.25

    # 标准格式参考值
    STANDARD_FONTS = {
        "title": {"font": "黑体", "size": 22, "bold": True, "alignment": "center"},
        "heading1": {"font": "黑体", "size": 16, "bold": True},
        "heading2": {"font": "黑体", "size": 14, "bold": True},
        "body": {"font": "宋体", "size": 12, "line_spacing": 1.5,
                "first_line_indent": True},
    }

    def score(self, file_path: str,
             expected_format: Dict[str, Any] | None = None) -> WordScoreResult:
        """
        评分Word文档

        Args:
            file_path: docx文件路径
            expected_format: 期望的格式要求（可选）
        """
        result = WordScoreResult(file_path=file_path)

        if not os.path.exists(file_path):
            result.issues.append(f"文件不存在: {file_path}")
            return result

        try:
            doc = Document(file_path)
        except Exception as e:
            result.issues.append(f"无法打开文件: {e}")
            return result

        # 分析文档
        paragraphs_info = self._analyze_paragraphs(doc)
        headings_info = self._analyze_headings(doc, paragraphs_info)
        format_info = self._analyze_format(doc, paragraphs_info)
        consistency_info = self._analyze_consistency(paragraphs_info)

        # 计算各维度分数
        result.format_accuracy = self._score_format(format_info, expected_format)
        result.heading_recognition = self._score_headings(headings_info)
        result.layout_consistency = self._score_consistency(consistency_info)

        result.format_details = format_info
        result.heading_details = headings_info
        result.layout_details = consistency_info

        # 总分（加权平均）
        result.total_score = (
            result.format_accuracy * self.WEIGHT_FORMAT_ACCURACY +
            result.heading_recognition * self.WEIGHT_HEADING_RECOGNITION +
            result.layout_consistency * self.WEIGHT_LAYOUT_CONSISTENCY
        )

        # 生成问题和建议
        result.issues = self._collect_issues(format_info, headings_info, consistency_info)
        result.suggestions = self._collect_suggestions(format_info, headings_info, consistency_info)

        return result

    def _analyze_paragraphs(self, doc: DocumentType) -> List[Dict[str, Any]]:
        """分析所有段落"""
        paragraphs = []
        for i, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            if not text:
                continue

            info: Dict[str, Any] = {
                "index": i,
                "text": text[:50],
                "text_length": len(text),
                "style_name": para.style.name if para.style else "",
                "alignment": str(para.alignment) if para.alignment else "None",
                "first_line_indent": None,
                "line_spacing": None,
                "runs": [],
            }

            # 段落格式
            pf = para.paragraph_format
            if pf.first_line_indent:
                info["first_line_indent"] = pf.first_line_indent.pt if hasattr(pf.first_line_indent, 'pt') else None
            if pf.line_spacing:
                info["line_spacing"] = pf.line_spacing

            # Run级别格式
            for run in para.runs:
                run_info: Dict[str, Any] = {
                    "text": run.text[:30],
                    "font_name": run.font.name,
                    "font_size": run.font.size.pt if run.font.size else None,
                    "bold": run.font.bold,
                    "italic": run.font.italic,
                    "color": None,
                }
                if run.font.color and run.font.color.rgb:
                    run_info["color"] = str(run.font.color.rgb)
                info["runs"].append(run_info)

            # 中文正文优先从包含中文的 run 取主字体；再按承载字符数选择，
            # 避免段首英文/数字 run 把西文字体误当作中文主字体。
            candidates = [r for r in info["runs"] if r["font_name"] and r["text"]]
            cjk_candidates = [r for r in candidates if re.search(r"[\u3400-\u9fff]", r["text"])]
            dominant = max(cjk_candidates or candidates,
                           key=lambda item: len(item["text"]), default=None)
            if dominant:
                info["main_font"] = dominant["font_name"]
                info["main_size"] = dominant["font_size"]
                info["main_bold"] = dominant["bold"]
            else:
                info["main_font"] = None
                info["main_size"] = None
                info["main_bold"] = None

            paragraphs.append(info)

        return paragraphs

    # 手动标题（无标题样式、加粗加大）判定所用的相对字号倍率：
    # 候选项需达到正文字号的 1.25 倍，达到 1.5 倍视为一级标题。
    _MANUAL_HEADING_RATIO = 1.25
    _MANUAL_HEADING_LEVEL1_RATIO = 1.5

    def _analyze_headings(self, doc: DocumentType,
                         paragraphs: List[Dict]) -> Dict[str, Any]:
        """分析标题"""
        headings = []
        heading_styles_found = set()
        title_found = False

        # 文档内相对字号基准：出现次数最多的字号视为正文字号，
        # 手动标题按相对倍率判断，替代硬编码的 16/18pt 阈值。
        size_counts = Counter(p["main_size"] for p in paragraphs
                              if p["main_size"])
        body_size = size_counts.most_common(1)[0][0] if size_counts else None

        for p in paragraphs:
            style = p["style_name"]

            # 判断是否为标题
            is_heading = False
            heading_level = 0

            if style == "Title" or style == "标题":
                is_heading = True
                heading_level = 0  # 文档标题
                title_found = True
            elif style.startswith("Heading") or style.startswith("标题"):
                is_heading = True
                try:
                    level_match = re.search(r"(?:Heading|标题)\s*(\d+)", style, re.IGNORECASE)
                    heading_level = int(level_match.group(1)) if level_match else 1
                except ValueError:
                    heading_level = 1
            elif (p["main_bold"] and p["main_size"] and body_size
                  and p["main_size"] >= body_size * self._MANUAL_HEADING_RATIO):
                # 加粗且相对正文字号明显更大，可能是手动设置的标题
                is_heading = True
                heading_level = (1 if p["main_size"] >= body_size * self._MANUAL_HEADING_LEVEL1_RATIO
                                 else 2)

            if is_heading:
                headings.append({
                    "text": p["text"],
                    "level": heading_level,
                    "style": style,
                    "font": p["main_font"],
                    "size": p["main_size"],
                    "bold": p["main_bold"],
                    "is_manual": not (style.startswith("Heading") or style.startswith("标题") or style == "Title"),
                })
                heading_styles_found.add(style)

        # 检测标题层级
        levels = sorted(set(h["level"] for h in headings))

        # 检测是否有层级跳跃
        level_gaps = []
        for i in range(1, len(levels)):
            if levels[i] - levels[i-1] > 1:
                level_gaps.append(f"层级跳跃: {levels[i-1]}→{levels[i]}")

        return {
            "total_headings": len(headings),
            "title_found": title_found,
            "heading_levels": levels,
            "heading_styles": list(heading_styles_found),
            "headings": headings[:20],  # 最多返回20个
            "level_gaps": level_gaps,
            "manual_headings": sum(1 for h in headings if h["is_manual"]),
            "styled_headings": sum(1 for h in headings if not h["is_manual"]),
        }

    def _analyze_format(self, doc: DocumentType,
                       paragraphs: List[Dict]) -> Dict[str, Any]:
        """分析格式"""
        if not paragraphs:
            return {"error": "无段落"}

        # 统计字体使用
        font_usage: Dict[str, int] = {}
        size_usage: Dict[float, int] = {}
        bold_count = 0
        alignment_usage: Dict[str, int] = {}
        indented_count = 0
        line_spacing_usage: Dict[float, int] = {}

        body_paragraphs = []  # 正文段落（非标题）

        for p in paragraphs:
            # 跳过标题
            style = p["style_name"]
            if style.startswith("Heading") or style.startswith("标题") or style == "Title":
                continue
            if p["main_bold"] and p["main_size"] and p["main_size"] >= 16:
                continue

            body_paragraphs.append(p)

            if p["main_font"]:
                font_usage[p["main_font"]] = font_usage.get(p["main_font"], 0) + 1
            if p["main_size"]:
                size_usage[p["main_size"]] = size_usage.get(p["main_size"], 0) + 1
            if p["main_bold"]:
                bold_count += 1
            if p["alignment"]:
                alignment_usage[p["alignment"]] = alignment_usage.get(p["alignment"], 0) + 1
            if p["first_line_indent"] and p["first_line_indent"] > 0:
                indented_count += 1
            if p["line_spacing"]:
                ls = p["line_spacing"]
                line_spacing_usage[ls] = line_spacing_usage.get(ls, 0) + 1

        total_body = len(body_paragraphs) if body_paragraphs else 1

        # 找主要字体和字号
        main_font = max(font_usage, key=lambda k: font_usage[k]) if font_usage else None
        main_size = max(size_usage, key=lambda k: size_usage[k]) if size_usage else None

        # 字体一致性比例
        font_consistency = font_usage.get(main_font, 0) / total_body if main_font else 0
        size_consistency = size_usage.get(main_size, 0) / total_body if main_size else 0

        # 首行缩进比例
        indent_ratio = indented_count / total_body

        # 行距设置比例
        ls_ratio = sum(line_spacing_usage.values()) / total_body

        # 对齐方式
        main_alignment = max(alignment_usage, key=lambda k: alignment_usage[k]) if alignment_usage else None

        return {
            "total_paragraphs": len(paragraphs),
            "body_paragraphs": len(body_paragraphs),
            "main_font": main_font,
            "main_size": main_size,
            "font_usage": font_usage,
            "size_usage": size_usage,
            "font_consistency": round(font_consistency, 2),
            "size_consistency": round(size_consistency, 2),
            "indent_ratio": round(indent_ratio, 2),
            "line_spacing_ratio": round(ls_ratio, 2),
            "main_alignment": main_alignment,
            "alignment_usage": alignment_usage,
            "bold_body_ratio": round(bold_count / total_body, 2),
        }

    def _analyze_consistency(self, paragraphs: List[Dict]) -> Dict[str, Any]:
        """分析排版一致性"""
        if len(paragraphs) < 2:
            return {"consistent": True, "score": 100}

        # 检查同类段落格式是否一致
        body_paras = []
        for p in paragraphs:
            style = p["style_name"]
            if style.startswith("Heading") or style.startswith("标题") or style == "Title":
                continue
            if p["main_bold"] and p["main_size"] and p["main_size"] >= 16:
                continue
            body_paras.append(p)

        if len(body_paras) < 2:
            return {"consistent": True, "score": 100, "body_count": len(body_paras)}

        # 检查正文字体一致性
        fonts = set(p["main_font"] for p in body_paras if p["main_font"])
        sizes = set(p["main_size"] for p in body_paras if p["main_size"])
        alignments = set(p["alignment"] for p in body_paras)

        # 检查缩进一致性
        indents = set()
        for p in body_paras:
            if p["first_line_indent"] is not None:
                indents.add(round(p["first_line_indent"], 1))

        inconsistencies = []
        if len(fonts) > 2:
            inconsistencies.append(f"正文字体不统一: {fonts}")
        if len(sizes) > 2:
            inconsistencies.append(f"正文字号不统一: {sizes}")
        if len(indents) > 2:
            inconsistencies.append(f"首行缩进不统一: {indents}")

        # 计算一致性分数
        score = 100
        if len(fonts) > 1:
            score -= min(20, (len(fonts) - 1) * 10)
        if len(sizes) > 1:
            score -= min(20, (len(sizes) - 1) * 10)
        if len(indents) > 1:
            score -= min(15, (len(indents) - 1) * 8)
        if len(alignments) > 2:
            score -= 10

        return {
            "score": max(0, score),
            "body_count": len(body_paras),
            "fonts_found": list(fonts),
            "sizes_found": list(sizes),
            "indents_found": list(indents),
            "alignments_found": list(alignments),
            "inconsistencies": inconsistencies,
            "consistent": len(inconsistencies) == 0,
        }

    def _score_format(self, format_info: Dict,
                     expected: Dict | None = None) -> float:
        """格式正确率评分"""
        if "error" in format_info:
            return 0

        score = 60  # 基础分

        # 有统一的正文字体
        if format_info.get("main_font"):
            score += 10
        # 有统一的正文字号
        if format_info.get("main_size"):
            score += 10
        # 字体一致性高
        if format_info.get("font_consistency", 0) >= 0.8:
            score += 8
        elif format_info.get("font_consistency", 0) >= 0.6:
            score += 4
        # 字号一致性高
        if format_info.get("size_consistency", 0) >= 0.8:
            score += 8
        elif format_info.get("size_consistency", 0) >= 0.6:
            score += 4
        # 设置了首行缩进
        if format_info.get("indent_ratio", 0) >= 0.5:
            score += 7
        # 设置了行距
        if format_info.get("line_spacing_ratio", 0) >= 0.5:
            score += 7

        # 如果提供期望格式，以用户期望参与评分，而不是始终按内置模板打分。
        if expected:
            expected_font = expected.get("body_font", expected.get("font"))
            expected_size = expected.get("body_size", expected.get("size"))
            expected_indent = expected.get("first_line_indent")
            expected_spacing = expected.get("line_spacing")
            if expected_font and format_info.get("main_font") != expected_font:
                score -= 15
            if expected_size is not None and format_info.get("main_size") is not None:
                try:
                    if abs(float(format_info["main_size"]) - float(expected_size)) > 0.5:
                        score -= 15
                except (TypeError, ValueError):
                    score -= 5
            if expected_indent and format_info.get("indent_ratio", 0) < 0.8:
                score -= 10
            if expected_spacing and format_info.get("line_spacing_ratio", 0) < 0.8:
                score -= 10

        return max(0, min(100, score))

    def _score_headings(self, heading_info: Dict) -> float:
        """标题识别率评分"""
        total = heading_info.get("total_headings", 0)
        if total == 0:
            return 30  # 没有标题，低分

        score = 50  # 基础分

        # 有文档标题
        if heading_info.get("title_found"):
            score += 10

        # 使用了标题样式（非手动设置）
        styled = heading_info.get("styled_headings", 0)
        if total > 0:
            style_ratio = styled / total
            if style_ratio >= 0.8:
                score += 20
            elif style_ratio >= 0.5:
                score += 12
            elif style_ratio > 0:
                score += 5

        # 有多个层级
        levels = heading_info.get("heading_levels", [])
        if len(levels) >= 3:
            score += 10
        elif len(levels) >= 2:
            score += 5

        # 无层级跳跃
        if not heading_info.get("level_gaps"):
            score += 10

        return min(100, score)

    def _score_consistency(self, consistency_info: Dict) -> float:
        """排版一致性评分"""
        return consistency_info.get("score", 80)

    def _collect_issues(self, fmt, head, layout) -> List[str]:
        issues = []
        if fmt.get("font_consistency", 1) < 0.6:
            issues.append("正文字体不统一")
        if fmt.get("size_consistency", 1) < 0.6:
            issues.append("正文字号不统一")
        if fmt.get("indent_ratio", 1) < 0.3 and fmt.get("body_paragraphs", 0) > 3:
            issues.append("多数正文段落未设置首行缩进")
        if head.get("total_headings", 0) == 0:
            issues.append("未检测到标题结构")
        if head.get("manual_headings", 0) > head.get("styled_headings", 0):
            issues.append("多数标题为手动设置格式，未使用标题样式")
        if head.get("level_gaps"):
            issues.extend(head["level_gaps"])
        if layout.get("inconsistencies"):
            issues.extend(layout["inconsistencies"])
        return issues

    def _collect_suggestions(self, fmt, head, layout) -> List[str]:
        suggestions = []
        if fmt.get("indent_ratio", 1) < 0.5:
            suggestions.append("建议正文段落设置首行缩进2字符")
        if fmt.get("line_spacing_ratio", 1) < 0.5:
            suggestions.append("建议设置1.5倍行距")
        if head.get("styled_headings", 0) == 0 and head.get("total_headings", 0) > 0:
            suggestions.append("建议使用Word内置标题样式而非手动设置格式")
        if len(layout.get("fonts_found", [])) > 2:
            suggestions.append("建议统一正文字体")
        return suggestions
