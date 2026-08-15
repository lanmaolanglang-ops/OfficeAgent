"""
PPT Orchestrator - PPT 生成总控

流程：
用户需求 → 内容规划 → 模板解析 → 视觉设计 → 生成文件 → 质量检查 → 输出
"""
import logging
from pathlib import Path
from typing import Optional

from .models import PPTOutline, PPTGenerationResult, SlideContent
from .content_planner import ContentPlanner
from .template_analyzer import TemplateAnalyzer
from .slide_designer import SlideDesigner
from .ppt_service import PPTService
from .quality_checker import PPTQualityChecker, check_and_fix_outline

logger = logging.getLogger("office_agent.ppt.orchestrator")


class PPTOrchestrator:
    """
    PPT 生成总控

    用法:
        orch = PPTOrchestrator()

        # 从主题生成
        result = orch.generate_from_theme("AI项目汇报", slide_count=10, style="professional")

        # 从文本生成
        result = orch.generate_from_text(text_content, style="tech")

        # 从 Word 文档生成
        result = orch.generate_from_word("document.docx", style="academic")

        # 从大纲数据生成
        result = orch.generate_from_outline("标题", slides_data, output_path="out.pptx")
    """

    def __init__(self, model_gateway=None, image_gateway=None):
        self.model_gateway = model_gateway
        self.image_gateway = image_gateway
        self.planner = ContentPlanner(model_gateway=model_gateway)
        self.template_analyzer = TemplateAnalyzer()
        self.service = PPTService()
        self.quality_checker = PPTQualityChecker()

    def generate_from_theme(self, theme: str,
                            slide_count: int = 10,
                            style: str = "professional",
                            subtitle: str = "",
                            author: str = "",
                            template_path: str = "",
                            output_path: str = "") -> PPTGenerationResult:
        """
        从主题生成 PPT

        Args:
            theme: PPT 主题
            slide_count: 页数
            style: 风格 (professional/minimal/creative/tech/academic/nature)
            subtitle: 副标题
            author: 作者
            template_path: 模板路径（可选）
            output_path: 输出路径
        """
        try:
            # 1. 内容规划
            outline = self.planner.plan_from_theme(
                theme=theme,
                slide_count=slide_count,
                style=style,
                subtitle=subtitle,
                author=author,
            )

            # 2. 模板解析（如有）
            if template_path and Path(template_path).exists():
                outline = self.template_analyzer.apply_to_outline(template_path, outline)

            # 3. 视觉设计
            designer = SlideDesigner(
                style=style,
                color_scheme=outline.color_scheme,
                font_scheme=outline.font_scheme,
            )
            outline = designer.design(outline)

            # 3.5 给标记需要配图的页（content_image）按描述词生图
            outline = self._generate_marked_images(outline)

            # 3.6 生成前质量预检 + 自动修正
            outline = self._pre_check_and_fix(outline, expected_slides=slide_count)

            # 4. 生成文件
            if not output_path:
                safe_name = theme.replace(" ", "_").replace("/", "_")[:30]
                output_path = f"{safe_name}.pptx"

            result = self.service.generate(outline, output_path)
            if not result.success:
                return result

            # 5. 质量检查
            quality = self.quality_checker.check(output_path, expected_slides=slide_count)
            result.quality_score = quality.score
            result.quality_issues = [i.to_dict() for i in quality.issues]

            result.changes.append(f"质量分数: {quality.score:.0f}/100")

            return result

        except Exception as e:
            return PPTGenerationResult(
                success=False,
                message=f"PPT 生成失败: {str(e)}",
            )

    def generate_from_text(self, text: str,
                           style: str = "professional",
                           title: str = "",
                           output_path: str = "") -> PPTGenerationResult:
        """
        从文本内容生成 PPT

        支持 Markdown 格式：
        # 标题 → 封面
        ## 章节 → 章节页
        ### 页标题 → 内容页
        - 要点 → 列表
        """
        try:
            # 1. 内容规划
            outline = self.planner.plan_from_text(text, style=style, title=title)

            # 2. 视觉设计
            designer = SlideDesigner(style=style)
            outline = designer.design(outline)

            # 3. 生成
            if not output_path:
                output_path = "content_presentation.pptx"

            result = self.service.generate(outline, output_path)
            if not result.success:
                return result

            # 4. 质量检查
            quality = self.quality_checker.check(output_path)
            result.quality_score = quality.score
            result.quality_issues = [i.to_dict() for i in quality.issues]

            return result

        except Exception as e:
            return PPTGenerationResult(
                success=False,
                message=f"PPT 生成失败: {str(e)}",
            )

    def generate_from_word(self, docx_path: str,
                           style: str = "professional",
                           output_path: str = "") -> PPTGenerationResult:
        """
        从 Word 文档生成 PPT

        自动分析文档结构，将章节标题转为 PPT 页面。
        """
        try:
            if not Path(docx_path).exists():
                return PPTGenerationResult(
                    success=False,
                    message=f"文件不存在: {docx_path}",
                )

            # 1. 从 Word 提取大纲
            outline = self.planner.plan_from_word(docx_path, style=style)

            # 2. 视觉设计
            designer = SlideDesigner(style=style)
            outline = designer.design(outline)

            # 3. 生成
            if not output_path:
                stem = Path(docx_path).stem
                output_path = f"{stem}_presentation.pptx"

            result = self.service.generate(outline, output_path)
            if not result.success:
                return result

            # 4. 质量检查
            quality = self.quality_checker.check(output_path)
            result.quality_score = quality.score
            result.quality_issues = [i.to_dict() for i in quality.issues]

            return result

        except Exception as e:
            return PPTGenerationResult(
                success=False,
                message=f"PPT 生成失败: {str(e)}",
            )

    def generate_from_outline(self, title: str,
                              slides_data: list,
                              style: str = "professional",
                              output_path: str = "") -> PPTGenerationResult:
        """
        从结构化大纲数据生成 PPT

        slides_data: [
            {"layout": "cover", "title": "..."},
            {"layout": "content", "title": "...", "bullets": ["...", "..."]},
            {"layout": "data_cards", "title": "...", "data": [("用户", "100", "万")]},
            ...
        ]
        """
        try:
            # 1. 构建大纲
            outline = self.planner.plan_from_outline_data(
                title=title,
                slides_data=slides_data,
                style=style,
            )

            # 2. 视觉设计
            designer = SlideDesigner(style=style)
            outline = designer.design(outline)

            # 3. 生成
            if not output_path:
                safe_name = title.replace(" ", "_")[:30] if title else "presentation"
                output_path = f"{safe_name}.pptx"

            result = self.service.generate(outline, output_path)
            if not result.success:
                return result

            # 4. 质量检查
            quality = self.quality_checker.check(output_path)
            result.quality_score = quality.score
            result.quality_issues = [i.to_dict() for i in quality.issues]

            return result

        except Exception as e:
            return PPTGenerationResult(
                success=False,
                message=f"PPT 生成失败: {str(e)}",
            )

    def generate_with_template(self, template_path: str,
                               theme: str = "",
                               text: str = "",
                               slides_data: list = None,
                               output_path: str = "") -> PPTGenerationResult:
        """
        使用指定模板生成 PPT

        可以从主题、文本或结构化数据生成内容，
        但使用模板的配色和字体。
        """
        try:
            if not Path(template_path).exists():
                return PPTGenerationResult(
                    success=False,
                    message=f"模板不存在: {template_path}",
                )

            # 分析模板
            template_info = self.template_analyzer.analyze(template_path)

            # 规划内容
            if slides_data:
                outline = self.planner.plan_from_outline_data(
                    title=theme or "演示文稿",
                    slides_data=slides_data,
                )
            elif text:
                outline = self.planner.plan_from_text(text, title=theme)
            elif theme:
                outline = self.planner.plan_from_theme(theme)
            else:
                return PPTGenerationResult(
                    success=False,
                    message="请提供主题、文本或大纲数据",
                )

            # 应用模板样式
            outline = self.template_analyzer.apply_to_outline(template_path, outline)

            # 视觉设计
            designer = SlideDesigner(
                color_scheme=outline.color_scheme,
                font_scheme=outline.font_scheme,
            )
            outline = designer.design(outline)

            # 生成
            if not output_path:
                stem = Path(template_path).stem
                output_path = f"{stem}_new.pptx"

            result = self.service.generate(outline, output_path)
            if not result.success:
                return result

            # 质量检查
            quality = self.quality_checker.check(output_path)
            result.quality_score = quality.score

            return result

        except Exception as e:
            return PPTGenerationResult(
                success=False,
                message=f"模板生成失败: {str(e)}",
            )

    def _generate_marked_images(self, outline: PPTOutline) -> PPTOutline:
        """为标记了 image_prompt 的 content_image 页生成配图（按需生图）"""
        if not self.image_gateway:
            return outline
        try:
            if not self.image_gateway.available():
                return outline
        except Exception:
            return outline
        import tempfile
        for slide in outline.slides:
            if slide.layout != "content_image" or slide.image_path:
                continue
            if not slide.image_prompt:
                continue
            try:
                slide.image_path = self.image_gateway.generate(
                    slide.image_prompt, output_dir=tempfile.gettempdir()
                )
                slide.image_alt = slide.image_prompt[:50]
            except Exception as e:
                logger.warning("第 %s 页配图生成失败: %s", slide.page_number, e)
        return outline

    def _pre_check_and_fix(self, outline: PPTOutline,
                           expected_slides: int = None) -> PPTOutline:
        """
        生成前检查 Outline 并自动修正可修复问题

        检查：内容完整性、要点数量、文字量、封面/总结页
        修正：减少过多要点、截断过长文字、补充封面/总结页、补标题
        """
        fixed, report = check_and_fix_outline(outline, expected_slides)
        if report.fixable_issues():
            fixed.changes = getattr(fixed, 'changes', [])
            fixed.changes.append(
                f"质量预检: 发现{len(report.issues)}个问题，"
                f"已自动修正{len(report.fixable_issues())}个"
            )
        return fixed

    def _post_check(self, output_path: str,
                    expected_slides: int = None) -> PPTGenerationResult:
        """
        生成后检查 .pptx 文件质量

        Returns:
            PPTGenerationResult with quality_score and quality_issues
        """
        from .models import PPTGenerationResult
        result = PPTGenerationResult(success=True, output_path=output_path)

        quality = self.quality_checker.check(output_path, expected_slides)
        result.quality_score = quality.score
        result.quality_issues = [i.to_dict() for i in quality.issues]
        result.slide_count = quality.slide_count
        result.message = f"PPT 生成成功，共{quality.slide_count}页，质量分{quality.score:.0f}"

        if not quality.passed:
            result.message += f"，{len(quality.errors())}个错误需关注"
        elif quality.warnings():
            result.message += f"，{len(quality.warnings())}个警告"

        return result
