"""
PPT Quality Scorer - PPT演示文稿质量评分引擎

评分维度：
1. 视觉评分 (visual_score): 配色、布局、字体、设计感
2. 内容完整度 (content_completeness): 内容是否完整、结构是否清晰
3. 模板遵循度 (template_adherence): 是否遵循统一模板/主题
"""
import os
from typing import Dict, List, Any
from dataclasses import dataclass, field

from pptx import Presentation
from pptx.util import Emu


@dataclass
class PPTScoreResult:
    """PPT评分结果"""
    file_path: str = ""
    total_score: float = 0.0

    # 各维度分数 (0-100)
    visual_score: float = 0.0
    content_completeness: float = 0.0
    template_adherence: float = 0.0

    # 详细信息
    visual_details: Dict[str, Any] = field(default_factory=dict)
    content_details: Dict[str, Any] = field(default_factory=dict)
    template_details: Dict[str, Any] = field(default_factory=dict)

    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "total_score": round(self.total_score, 1),
            "scores": {
                "visual_score": round(self.visual_score, 1),
                "content_completeness": round(self.content_completeness, 1),
                "template_adherence": round(self.template_adherence, 1),
            },
            "details": {
                "visual": self.visual_details,
                "content": self.content_details,
                "template": self.template_details,
            },
            "issues": self.issues,
            "suggestions": self.suggestions,
        }


class PPTQualityScorer:
    """PPT质量评分器"""

    # 总分权重（合计必须为 1.0）：视觉与内容并重，模板符合度略低
    WEIGHT_VISUAL = 0.35
    WEIGHT_CONTENT_COMPLETENESS = 0.35
    WEIGHT_TEMPLATE_ADHERENCE = 0.30

    def score(self, file_path: str,
             expected_slides: int = 0,
             required_content: List[str] | None = None) -> PPTScoreResult:
        """
        评分PPT

        Args:
            file_path: pptx文件路径
            expected_slides: 期望页数（0=不检查）
            required_content: 必须包含的内容关键词
        """
        result = PPTScoreResult(file_path=file_path)

        if not os.path.exists(file_path):
            result.issues.append(f"文件不存在: {file_path}")
            return result

        try:
            prs = Presentation(file_path)
        except Exception as e:
            result.issues.append(f"无法打开文件: {e}")
            return result

        # 分析幻灯片
        slides_info = self._analyze_slides(prs)

        # 各维度评分
        result.visual_score = self._score_visual(slides_info)
        result.content_completeness = self._score_content(
            slides_info, expected_slides, required_content
        )
        result.template_adherence = self._score_template(slides_info)

        result.visual_details = slides_info.get("visual", {})
        result.content_details = slides_info.get("content", {})
        result.template_details = slides_info.get("template", {})

        # 总分
        result.total_score = (
            result.visual_score * self.WEIGHT_VISUAL +
            result.content_completeness * self.WEIGHT_CONTENT_COMPLETENESS +
            result.template_adherence * self.WEIGHT_TEMPLATE_ADHERENCE
        )

        result.issues = self._collect_issues(
            slides_info, expected_slides, required_content
        )
        result.suggestions = self._collect_suggestions(slides_info)

        return result

    def _analyze_slides(self, prs: Presentation) -> Dict[str, Any]:
        """分析所有幻灯片"""
        slide_count = len(prs.slides)
        slide_width = prs.slide_width
        slide_height = prs.slide_height

        slides_data = []
        all_fonts = set()
        all_sizes = set()
        all_colors = set()
        title_count = 0
        text_box_count = 0
        shape_count = 0
        picture_count = 0
        chart_count = 0
        table_count = 0
        empty_slides = 0
        text_lengths = []

        # 位置一致性检查
        title_positions = []

        for slide_idx, slide in enumerate(prs.slides):
            slide_info = {
                "index": slide_idx,
                "shapes": 0,
                "has_title": False,
                "title_text": "",
                "text_boxes": 0,
                "pictures": 0,
                "charts": 0,
                "tables": 0,
                "text_length": 0,
                "fonts": set(),
                "sizes": set(),
                "colors": set(),
                "has_background": False,
            }

            slide_text = []

            for shape in slide.shapes:
                slide_info["shapes"] += 1
                shape_count += 1

                # 背景
                if shape.has_text_frame:
                    pass

                # 图片
                if shape.shape_type == 13:  # PICTURE
                    slide_info["pictures"] += 1
                    picture_count += 1

                # 图表
                if shape.has_chart:
                    slide_info["charts"] += 1
                    chart_count += 1

                # 表格
                if shape.has_table:
                    slide_info["tables"] += 1
                    table_count += 1

                # 文本
                if shape.has_text_frame:
                    slide_info["text_boxes"] += 1
                    text_box_count += 1

                    for para in shape.text_frame.paragraphs:
                        for run in para.runs:
                            text = run.text.strip()
                            if text:
                                slide_text.append(text)
                                slide_info["text_length"] += len(text)

                                if run.font.name:
                                    slide_info["fonts"].add(run.font.name)
                                    all_fonts.add(run.font.name)
                                if run.font.size:
                                    slide_info["sizes"].add(run.font.size.pt)
                                    all_sizes.add(run.font.size.pt)
                                try:
                                    if run.font.color and run.font.color.type is not None:
                                        rgb = run.font.color.rgb
                                        if rgb:
                                            color_str = str(rgb)
                                            slide_info["colors"].add(color_str)
                                            all_colors.add(color_str)
                                except (AttributeError, ValueError):
                                    pass

                    # 判断是否为标题
                    title_shape = slide.shapes.title
                    if (title_shape is not None and shape._element is title_shape._element) or (
                        shape.top and shape.top < Emu(2000000) and
                        any(run.font.size and run.font.size.pt >= 24
                            for para in shape.text_frame.paragraphs
                            for run in para.runs)
                    ):
                        slide_info["has_title"] = True
                        slide_info["title_text"] = shape.text_frame.text[:50]
                        title_count += 1
                        if shape.top:
                            title_positions.append(shape.top)

            text_lengths.append(slide_info["text_length"])

            if slide_info["text_length"] == 0 and slide_info["pictures"] == 0:
                empty_slides += 1

            # 转换set为list以便序列化
            slide_info["fonts"] = list(slide_info["fonts"])
            slide_info["sizes"] = list(slide_info["sizes"])
            slide_info["colors"] = list(slide_info["colors"])
            slides_data.append(slide_info)

        # 计算统计
        avg_text_length = sum(text_lengths) / max(len(text_lengths), 1)
        avg_shapes = shape_count / max(slide_count, 1)

        # 字体一致性
        font_consistency = 1.0
        if len(all_fonts) > 3:
            font_consistency = max(0.3, 1.0 - (len(all_fonts) - 3) * 0.15)

        # 颜色一致性
        color_consistency = 1.0
        if len(all_colors) > 5:
            color_consistency = max(0.3, 1.0 - (len(all_colors) - 5) * 0.1)

        # 标题位置一致性
        title_pos_consistency = 1.0
        if len(title_positions) >= 3:
            positions = [p for p in title_positions if p is not None]
            if positions:
                avg_pos = sum(positions) / len(positions)
                variance = sum(abs(p - avg_pos) for p in positions) / len(positions)
                if variance > Emu(500000):
                    title_pos_consistency = max(0.5, 1.0 - variance / Emu(5000000))

        return {
            "slide_count": slide_count,
            "slide_width": slide_width,
            "slide_height": slide_height,
            "title_count": title_count,
            "text_box_count": text_box_count,
            "shape_count": shape_count,
            "picture_count": picture_count,
            "chart_count": chart_count,
            "table_count": table_count,
            "empty_slides": empty_slides,
            "avg_text_length": round(avg_text_length, 1),
            "avg_shapes_per_slide": round(avg_shapes, 1),
            "all_fonts": list(all_fonts),
            "all_sizes": list(all_sizes),
            "all_colors": list(all_colors),
            "font_count": len(all_fonts),
            "size_count": len(all_sizes),
            "color_count": len(all_colors),
            "font_consistency": round(font_consistency, 2),
            "color_consistency": round(color_consistency, 2),
            "title_position_consistency": round(title_pos_consistency, 2),
            "slides": slides_data,
            "all_text": "\n".join(
                text
                for slide in prs.slides
                for shape in slide.shapes
                if getattr(shape, "has_text_frame", False)
                for text in [shape.text_frame.text]
                if text
            ),
            "visual": {
                "font_count": len(all_fonts),
                "color_count": len(all_colors),
                "picture_count": picture_count,
                "chart_count": chart_count,
                "avg_shapes": round(avg_shapes, 1),
                "font_consistency": round(font_consistency, 2),
                "color_consistency": round(color_consistency, 2),
            },
            "content": {
                "slide_count": slide_count,
                "title_count": title_count,
                "empty_slides": empty_slides,
                "avg_text_length": round(avg_text_length, 1),
                "has_charts": chart_count > 0,
                "has_tables": table_count > 0,
                "has_pictures": picture_count > 0,
            },
            "template": {
                "font_consistency": round(font_consistency, 2),
                "color_consistency": round(color_consistency, 2),
                "title_position_consistency": round(title_pos_consistency, 2),
                "title_coverage": round(title_count / max(slide_count, 1), 2),
            },
        }

    def _score_visual(self, info: Dict) -> float:
        """视觉评分"""
        score = 50  # 基础分

        # 字体数量合理（2-4种）
        fc = info["font_count"]
        if 1 <= fc <= 3:
            score += 15
        elif fc <= 5:
            score += 8
        elif fc > 5:
            score -= min(15, (fc - 5) * 3)

        # 颜色数量合理
        cc = info["color_count"]
        if 1 <= cc <= 5:
            score += 15
        elif cc <= 8:
            score += 8
        elif cc > 8:
            score -= min(15, (cc - 8) * 2)

        # 有图片或图表
        if info["picture_count"] > 0:
            score += 8
        if info["chart_count"] > 0:
            score += 8

        # 每页形状数量适中（不是太空也不是太满）
        avg_shapes = info["avg_shapes_per_slide"]
        if 3 <= avg_shapes <= 8:
            score += 10
        elif 2 <= avg_shapes <= 12:
            score += 5

        # 字体一致性
        score += info["font_consistency"] * 5
        # 颜色一致性
        score += info["color_consistency"] * 5

        return max(0, min(100, score))

    def _score_content(self, info: Dict, expected_slides: int,
                      required_content: List[str] | None) -> float:
        """内容完整度评分"""
        score = 50

        # 页数合理
        sc = info["slide_count"]
        if sc >= 3:
            score += 15
        elif sc >= 1:
            score += 5

        if expected_slides > 0:
            ratio = sc / expected_slides
            if 0.8 <= ratio <= 1.2:
                score += 10
            elif ratio >= 0.5:
                score += 5

        # 有标题
        title_ratio = info["title_count"] / max(sc, 1)
        if title_ratio >= 0.8:
            score += 15
        elif title_ratio >= 0.5:
            score += 8

        # 无空页
        if info["empty_slides"] == 0:
            score += 10
        elif info["empty_slides"] <= sc * 0.2:
            score += 5

        # 内容量适中
        avg_text = info["avg_text_length"]
        if 50 <= avg_text <= 300:
            score += 10
        elif 20 <= avg_text <= 500:
            score += 5

        # 用户明确要求的内容必须真正出现在成品中。按关键词逐项计分，
        # 全部缺失时不应仍得到高完整度分。
        required = [str(item).strip() for item in (required_content or []) if str(item).strip()]
        if required:
            all_text = info.get("all_text", "").casefold()
            matched = sum(1 for item in required if item.casefold() in all_text)
            ratio = matched / len(required)
            score += ratio * 10
            score -= (1 - ratio) * 30

        return max(0, min(100, score))

    def _score_template(self, info: Dict) -> float:
        """模板遵循度评分"""
        score = 50

        # 字体一致性
        score += info["font_consistency"] * 15
        # 颜色一致性
        score += info["color_consistency"] * 15
        # 标题位置一致性
        score += info["title_position_consistency"] * 10
        # 标题覆盖率
        title_cov = info["template"]["title_coverage"]
        if title_cov >= 0.8:
            score += 10
        elif title_cov >= 0.5:
            score += 5

        return max(0, min(100, score))

    def _collect_issues(self, info, expected_slides,
                        required_content: List[str] | None = None) -> List[str]:
        issues = []
        if info["font_count"] > 5:
            issues.append(f"字体种类过多({info['font_count']}种)，建议统一为2-3种")
        if info["color_count"] > 8:
            issues.append(f"颜色种类过多({info['color_count']}种)，建议使用统一配色方案")
        if info["empty_slides"] > 0:
            issues.append(f"存在{info['empty_slides']}页空白幻灯片")
        if info["title_count"] < info["slide_count"] * 0.5:
            issues.append("部分幻灯片缺少标题")
        if expected_slides > 0 and info["slide_count"] < expected_slides * 0.7:
            issues.append(f"页数不足（期望{expected_slides}页，实际{info['slide_count']}页）")
        required = [str(item).strip() for item in (required_content or []) if str(item).strip()]
        if required:
            all_text = info.get("all_text", "").casefold()
            missing = [item for item in required if item.casefold() not in all_text]
            if missing:
                issues.append(f"缺少必含内容: {', '.join(missing)}")
        return issues

    def _collect_suggestions(self, info) -> List[str]:
        suggestions = []
        if info["font_count"] > 3:
            suggestions.append("统一字体，标题和正文各用一种字体即可")
        if info["picture_count"] == 0 and info["chart_count"] == 0:
            suggestions.append("适当添加图片或图表增强视觉效果")
        if info["avg_text_length"] > 400:
            suggestions.append("部分页面文字过多，建议精简内容")
        if info["title_position_consistency"] < 0.8:
            suggestions.append("统一标题位置，使用母版或布局模板")
        return suggestions
