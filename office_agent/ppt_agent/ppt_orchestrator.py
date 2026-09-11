"""
PPT Orchestrator - PPT 生成总控

流程：
用户需求 → 内容规划 → 模板解析 → 视觉设计 → 生成文件 → 质量检查 → 输出
"""
import logging
import re
import tempfile
from pathlib import Path
from typing import TypedDict

from ..security.error_sanitizer import sanitize_error
from ..quality.checker import IssueSeverity

from .models import PPTOutline, PPTGenerationResult
from .content_planner import ContentPlanner
from .template_analyzer import TemplateAnalyzer
from .slide_designer import SlideDesigner
from .ppt_service import PPTService
from .quality_checker import PPTQualityChecker, check_and_fix_outline

logger = logging.getLogger("office_agent.ppt.orchestrator")


def _safe_presentation_name(value: str, fallback: str = "presentation") -> str:
    """Create a Windows-safe basename and prevent relative-path traversal."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or ""))
    name = re.sub(r"_+", "_", name).strip(" ._")[:30]
    return name or fallback


class _ImageGenState(TypedDict):
    """配图生成状态：是否可用、尝试/成功计数与错误记录。"""
    configured: bool
    attempted: int
    generated: int
    errors: list[str]


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

    def __init__(self, model_gateway=None, image_gateway=None,
                 max_generated_images: int = 3):
        self.model_gateway = model_gateway
        self.image_gateway = image_gateway
        self.max_generated_images = max(0, min(int(max_generated_images), 8))
        self.image_generation: _ImageGenState = {
            "configured": False,
            "attempted": 0,
            "generated": 0,
            "errors": [],
        }
        self._generated_temp_images: list[str] = []
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
                safe_name = _safe_presentation_name(theme)
                output_path = f"{safe_name}.pptx"

            result = self._generate_file(outline, output_path)
            if not result.success:
                return result

            # 5. 质量检查
            self._attach_quality(result, output_path, slide_count)

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

            outline = self._generate_marked_images(outline)

            # 2.5 生成前质量预检 + 自动修正（与 theme 路径同一道关卡）
            outline = self._pre_check_and_fix(outline)

            # 3. 生成
            if not output_path:
                output_path = "content_presentation.pptx"

            result = self._generate_file(outline, output_path)
            if not result.success:
                return result

            # 4. 质量检查
            self._attach_quality(result, output_path)

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

            outline = self._generate_marked_images(outline)

            # 2.5 生成前质量预检 + 自动修正（与 theme 路径同一道关卡）
            outline = self._pre_check_and_fix(outline)

            # 3. 生成
            if not output_path:
                stem = Path(docx_path).stem
                output_path = f"{stem}_presentation.pptx"

            result = self._generate_file(outline, output_path)
            if not result.success:
                return result

            # 4. 质量检查
            self._attach_quality(result, output_path)

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

            outline = self._generate_marked_images(outline)

            # 2.5 生成前质量预检 + 自动修正（与 theme 路径同一道关卡）
            outline = self._pre_check_and_fix(outline)

            # 3. 生成
            if not output_path:
                safe_name = _safe_presentation_name(title)
                output_path = f"{safe_name}.pptx"

            result = self._generate_file(outline, output_path)
            if not result.success:
                return result

            # 4. 质量检查
            self._attach_quality(result, output_path)

            return result

        except Exception as e:
            return PPTGenerationResult(
                success=False,
                message=f"PPT 生成失败: {str(e)}",
            )

    def generate_with_template(self, template_path: str,
                               theme: str = "",
                               text: str = "",
                               slides_data: list | None = None,
                               slide_count: int = 10,
                               output_path: str = "") -> PPTGenerationResult:
        """
        使用指定模板生成 PPT

        可以从主题、文本或结构化数据生成内容，
        但使用模板的配色和字体。

        Args:
            slide_count: 期望页数，透传给 plan_from_theme（用户指令解析出的页数）
        """
        try:
            if not Path(template_path).exists():
                return PPTGenerationResult(
                    success=False,
                    message=f"模板不存在: {template_path}",
                )

            # 分析模板
            self.template_analyzer.analyze(template_path)

            # 规划内容
            if slides_data:
                outline = self.planner.plan_from_outline_data(
                    title=theme or "演示文稿",
                    slides_data=slides_data,
                )
            elif text:
                outline = self.planner.plan_from_text(text, title=theme)
            elif theme:
                outline = self.planner.plan_from_theme(theme, slide_count=slide_count)
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

            # 生成前质量预检 + 自动修正（与 theme 路径同一道关卡）
            outline = self._pre_check_and_fix(outline, expected_slides=slide_count)

            outline = self._generate_marked_images(outline)

            # 生成
            if not output_path:
                stem = Path(template_path).stem
                output_path = f"{stem}_new.pptx"

            # 以模板为基底生成（继承母版/主题/页面尺寸），输出路径必须不同于模板
            outline._base_template_path = template_path

            result = self._generate_file(outline, output_path)
            if not result.success:
                return result

            # 质量检查
            self._attach_quality(result, output_path)

            return result

        except Exception as e:
            return PPTGenerationResult(
                success=False,
                message=f"模板生成失败: {str(e)}",
            )

    def _generate_marked_images(self, outline: PPTOutline) -> PPTOutline:
        """为适合图文表达的内容页生成配图，并保证配置有效时真正发起调用。"""
        if not self.image_gateway or self.max_generated_images <= 0:
            return outline
        try:
            if not self.image_gateway.available():
                return outline
            self.image_generation["configured"] = True
        except Exception as exc:
            error = sanitize_error(exc)
            self.image_generation["errors"].append(error)
            logger.warning("配图服务可用性检查失败，降级为纯文本大纲: %s", error)
            return outline
        # 优先处理 LLM 明确标记的图文页；若模型没有标记任何页面，则从普通
        # 内容页中确定性选择，避免“配置了生图但规划结果全是文本”。
        # 用**列表下标**而不是 slide 对象本身做身份判定：
        # SlideContent 是 dataclass，`==` 比较的是字段值。两页内容完全相同的
        # 页面会互相"等于"，`slide not in candidates` 会把后一页误判成已在
        # 候选里，于是它永远拿不到配图。下标在本次遍历内稳定且唯一。
        candidate_indices = [
            idx for idx, slide in enumerate(outline.slides)
            if slide.layout == "content_image" and not slide.image_path
        ]
        already = set(candidate_indices)
        candidate_indices.extend(
            idx for idx, slide in enumerate(outline.slides)
            if slide.layout in ("content", "content_list")
            and not slide.image_path and idx not in already
        )

        for idx in candidate_indices:
            slide = outline.slides[idx]
            if self.image_generation["generated"] >= self.max_generated_images:
                break
            prompt = (slide.image_prompt or (
                f"{outline.title}商务演示配图，页面主题：{slide.title}。"
                f"核心内容：{'；'.join(str(item) for item in slide.bullets[:4])}。"
                "横向构图，专业、简洁、有清晰视觉主体，避免文字和水印。"
            )).strip()
            original_layout = slide.layout
            self.image_generation["attempted"] += 1
            try:
                image_path = self.image_gateway.generate(
                    prompt, output_dir=tempfile.gettempdir()
                )
                if not image_path or not Path(image_path).is_file():
                    raise RuntimeError("生图服务未生成可用文件")
                slide.layout = "content_image"
                slide.image_prompt = prompt
                slide.image_path = image_path
                slide.image_alt = prompt[:50]
                self._generated_temp_images.append(str(Path(image_path).resolve()))
                self.image_generation["generated"] += 1
            except Exception as e:
                slide.layout = original_layout
                error = sanitize_error(e)
                self.image_generation["errors"].append(error)
                logger.warning("第 %s 页配图生成失败: %s", slide.page_number, error)
                lowered = error.lower()
                if any(code in lowered for code in (
                    "http 400", "http 401", "http 403", "http 404", "http 429",
                    "getaddrinfo", "name resolution", "连接失败", "timed out", "timeout",
                )):
                    break
        return outline

    def _generate_file(self, outline: PPTOutline, output_path: str) -> PPTGenerationResult:
        """Render the deck, then remove only images generated in the OS temp directory."""
        try:
            return self.service.generate(outline, output_path)
        finally:
            temp_root = Path(tempfile.gettempdir()).resolve()
            for image_path in self._generated_temp_images:
                path = Path(image_path)
                try:
                    if path.is_file() and path.parent.resolve() == temp_root:
                        path.unlink()
                except OSError:
                    logger.warning("临时配图清理失败: %s", path.name)
            self._generated_temp_images.clear()

    def _pre_check_and_fix(self, outline: PPTOutline,
                           expected_slides: int | None = None) -> PPTOutline:
        """
        生成前检查 Outline 并自动修正可修复问题

        检查：内容完整性、要点数量、文字量、封面/总结页
        修正：减少过多要点、截断过长文字、补充封面/总结页、补标题

        所有生成入口都应调用一次（且只调用一次），否则只有 theme 路径
        享受预检，其余路径的过长要点 / 缺封面会直接带病进入渲染。
        预检本身失败时降级为"不修正"，并记录原因——它只是优化步骤，
        不应让整次生成失败，但也不能假装修好了。
        """
        try:
            fixed, report = check_and_fix_outline(outline, expected_slides)
        except Exception as exc:
            reason = sanitize_error(exc)
            logger.warning("质量预检失败，降级为未修正的大纲: %s", reason)
            outline.changes = list(getattr(outline, "changes", []) or [])
            outline.changes.append(f"质量预检: 执行失败，已跳过（{reason}）")
            return outline

        if report.fixable_issues():
            fixed.changes = getattr(fixed, 'changes', [])
            fixed.changes.append(
                f"质量预检: 发现{len(report.issues)}个问题，"
                f"已自动修正{len(report.fixable_issues())}个"
            )
        return fixed

    def _attach_quality(self, result: PPTGenerationResult, output_path: str,
                        expected_slides: int | None = None) -> None:
        """附加质量信息；检查器故障不得反向覆盖已成功生成的产物。"""
        try:
            quality = self.quality_checker.check(output_path, expected_slides)
        except Exception as exc:
            logger.exception("PPT 已生成，但质量检查失败: %s", output_path)
            warning = f"质量检查未完成：{sanitize_error(exc)}"
            result.quality_issues.append({
                "type": "quality_check", "severity": IssueSeverity.WARNING.value,
                "message": warning,
            })
            result.changes.append(warning)
            return
        result.quality_score = quality.score
        result.quality_issues = [
            issue.to_dict() for issue in getattr(quality, "issues", [])
        ]
        result.slide_count = getattr(quality, "slide_count", 0) or result.slide_count
        result.changes.append(f"质量分数: {quality.score:.0f}/100")
