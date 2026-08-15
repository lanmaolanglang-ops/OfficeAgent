"""
PPT 相关后台任务

任务流程：
    0%   初始化
    20%  内容规划
    40%  模板选择
    60%  幻灯片生成
    80%  样式调整
    100% 完成
"""
import os
import re
import time
import logging
import traceback
import json
from ...security.error_sanitizer import sanitize_error
from pathlib import Path

logger = logging.getLogger("office_agent.tasks.ppt")


def _maybe_generate_slide_images(ppt_path: str, prompt: str, options: dict) -> int:
    """Generate images for text-heavy slides that do not already contain one."""
    if options.get("generate_images", True) is False:
        return 0
    try:
        from ...image_generation import ImageGenerationGateway
        config = options.get("image_model_config") or {}
        gateway = ImageGenerationGateway(
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url", ""),
            model=config.get("model", ""),
            provider=config.get("provider", ""),
        )
        if not gateway.available():
            logger.info("未配置图像模型，跳过 PPT 图片生成")
            return 0
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation(ppt_path)
        slide_w = prs.slide_width / 914400
        slide_h = prs.slide_height / 914400

        def overlaps_text(slide, left, top, width, height):
            for shape in slide.shapes:
                if not getattr(shape, "has_text_frame", False) or not shape.text.strip():
                    continue
                sx, sy = shape.left / 914400, shape.top / 914400
                sw, sh = shape.width / 914400, shape.height / 914400
                if left < sx + sw and left + width > sx and top < sy + sh and top + height > sy:
                    return True
            return False

        def safe_position(slide):
            width, height, margin = min(5.2, slide_w * 0.38), min(3.6, slide_h * 0.55), 0.25
            candidates = [
                (slide_w - width - margin, margin),
                (margin, slide_h - height - margin),
                (slide_w - width - margin, slide_h - height - margin),
            ]
            for left, top in candidates:
                if left >= margin and top >= margin and not overlaps_text(slide, left, top, width, height):
                    return left, top, width
            return None

        generated = 0
        limit = max(0, min(int(options.get("max_generated_images", 3)), 8))
        for index, slide in enumerate(prs.slides):
            if generated >= limit:
                break
            has_picture = any(getattr(shape, "shape_type", None) == 13 for shape in slide.shapes)
            if has_picture:
                continue
            text = " ".join(shape.text.strip() for shape in slide.shapes
                            if getattr(shape, "has_text_frame", False) and shape.text.strip())
            if len(text) < 12:
                continue
            position = safe_position(slide)
            if not position:
                logger.info("第 %s 页没有安全图片区域，跳过配图", index + 1)
                continue
            image_path = gateway.generate(
                f"PPT第{index + 1}页配图：{prompt}。本页内容：{text[:500]}。"
                "横向构图，商务演示风格，避免文字，高清。",
                size="1024x768", output_dir=os.path.dirname(ppt_path),
            )
            left, top, width = position
            slide.shapes.add_picture(image_path, Inches(left), Inches(top), width=Inches(width))
            generated += 1
        prs.save(ppt_path)
        return generated
    except Exception as exc:
        logger.warning("PPT 图片生成失败，继续生成无图片版本: %s", exc)
        return 0

# 输出目录
OUTPUT_DIR = os.path.expanduser("~/.office_agent/outputs")


def _make_registered_name(input_path: str, output_path: str, options: dict) -> str:
    """Build the registered output filename for Storage (does not change task-internal naming)."""
    if options and options.get("output_filename"):
        return str(options["output_filename"])
    if input_path:
        stem = Path(input_path).stem
        if stem:
            return f"{stem}_presentation.pptx"
    if output_path:
        return Path(output_path).name or "presentation.pptx"
    return "presentation.pptx"


def _safe_filename(text: str, ext: str = ".pptx") -> str:
    """生成安全的文件名，避免中文/特殊字符问题"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # 用时间戳命名，避免编码问题
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(OUTPUT_DIR, f"ppt_{timestamp}{ext}")


def _understand_ppt_request(instruction: str, options: dict) -> str:
    """Use the configured LLM to resolve follow-up PPT requests into a clear brief."""
    if not instruction:
        return instruction
    try:
        from ...model_gateway import ModelGateway
        history = options.get("history", [])[-8:] if isinstance(options, dict) else []
        response = ModelGateway().chat(
            user_message=json.dumps({
                "request": instruction,
                "previous_instruction": options.get("previous_instruction", ""),
                "is_follow_up": bool(options.get("is_follow_up")),
                "history": history,
            }, ensure_ascii=False),
            system_prompt=("你是PPT任务解析器。把用户当前要求和历史反馈合并成明确的演示文稿制作简报。"
                           "保留主题、受众、页数、风格、内容、模板和修改要求。只输出简报正文，不要解释。"),
            task_type_str="ppt_content", temperature=0.2, max_tokens=1000,
        )
        if isinstance(options, dict):
            options["model_call"] = {
                "called": True, "success": bool(response.success),
                "model": getattr(response, "model_used", None),
                "provider": getattr(response, "provider", None),
                "fallback_used": not bool(response.success),
                "error": getattr(response, "error", ""),
                "attempts": (response.raw_response or {}).get("attempts", 1) if isinstance(getattr(response, "raw_response", None), dict) else 1,
            }
        if response.success and response.content.strip():
            return response.content.strip()
    except Exception as exc:
        if isinstance(options, dict):
            options["model_call"] = {"called": True, "success": False,
                                      "fallback_used": True, "error": str(exc), "attempts": 0}
        logger.warning("PPT LLM理解不可用，使用原始指令: %s", exc)
    return instruction


def generate_ppt(outline: str = None, input_path: str = None,
                 output_path: str = None, template_type: str = "business",
                 instruction: str = None, options: dict = None,
                 progress=None, _task_id: str = None, **kwargs) -> dict:
    """
    PPT 生成任务

    Args:
        outline: PPT大纲文本或主题
        input_path: 输入文件路径
        output_path: 输出路径
        template_type: 模板类型
        instruction: 用户原始指令
        options: 选项
        progress: 进度回调
        _task_id: 任务ID
    """
    options = options or {}
    result = {"status": "success", "output_files": [], "slides": 0}

    try:
        if progress:
            progress.update(5, "初始化PPT生成")

        # 调用 PPT Orchestrator（传入ModelGateway以启用AI内容生成）
        from ...ppt_agent.ppt_orchestrator import PPTOrchestrator
        try:
            from ...model_gateway import ModelGateway
            model_gateway = ModelGateway()

            # 如果前端传了模型配置，添加到网关
            if options and options.get("model_config"):
                mc = options["model_config"]
                if mc.get("api_key") and mc.get("enabled", True):
                    provider = mc.get("provider", "openai")
                    model = mc.get("model", "")
                    api_key = mc.get("api_key", "")
                    base_url = mc.get("base_url", "")
                    try:
                        frontend_model_id = f"{provider}-frontend"
                        model_gateway.add_provider(
                            provider=provider if provider in ["openai", "deepseek", "doubao", "qwen", "anthropic", "google"] else "custom",
                            api_key=api_key,
                            model=model,
                            base_url=base_url,
                            model_id=frontend_model_id,
                        )
                        # 将前端模型设为ppt_content路由首选（仅内存，不保存文件）
                        routing = model_gateway.model_manager._routing
                        for task_key in ["ppt_content", "simple_text", "chinese_writing"]:
                            if task_key in routing:
                                route_list = routing[task_key]
                                if frontend_model_id in route_list:
                                    route_list.remove(frontend_model_id)
                                route_list.insert(0, frontend_model_id)
                        logger.info(f"已添加前端模型配置并设为首选: {provider}/{model}")
                    except Exception as e:
                        logger.warning(f"添加前端模型失败: {e}")

            logger.info("ModelGateway已初始化，将使用AI生成PPT内容")
        except Exception as e:
            logger.warning(f"ModelGateway初始化失败，将使用模板: {e}")
            model_gateway = None
        orchestrator = PPTOrchestrator(model_gateway=model_gateway)

        # 确定主题和输出路径
        effective_instruction = _understand_ppt_request(instruction or outline or "", options)
        # The LLM-resolved brief must take precedence during follow-ups.
        theme = effective_instruction or outline or "演示文稿"
        style = template_type or "professional"
        if not output_path:
            output_path = _safe_filename(theme)

        if progress:
            progress.update(20, "规划PPT内容结构")

        if progress:
            progress.update(40, f"选择{style}风格模板")

        # 根据输入类型选择生成方法
        ppt_result = None

        if input_path and os.path.exists(input_path):
            if progress:
                progress.update(60, "从文件生成PPT...")
            ext = Path(input_path).suffix.lower()
            if ext in ('.docx', '.doc'):
                ppt_result = orchestrator.generate_from_word(
                    docx_path=input_path,
                    style=style,
                    output_path=output_path,
                )
            else:
                ppt_result = orchestrator.generate_from_text(
                    text=theme,
                    style=style,
                    output_path=output_path,
                )
        else:
            if progress:
                progress.update(60, "生成幻灯片内容...")
            ppt_result = orchestrator.generate_from_theme(
                theme=theme,
                style=style,
                output_path=output_path,
            )

        if progress:
            progress.update(80, "调整样式和布局")

        if ppt_result:
            if hasattr(ppt_result, 'success') and not ppt_result.success:
                error_msg = getattr(ppt_result, 'message', 'PPT生成失败')
                result["status"] = "failed"
                result["error"] = error_msg
                if progress:
                    progress.update(100, f"生成失败: {error_msg}")
                return result

            output = getattr(ppt_result, 'output_path', None) or getattr(ppt_result, 'output_file', None)
            slides = getattr(ppt_result, 'slide_count', 0) or getattr(ppt_result, 'slides_count', 0)
            message = getattr(ppt_result, 'message', '')

            if output and os.path.exists(str(output)):
                generated_images = _maybe_generate_slide_images(str(output), effective_instruction, options)
                if generated_images:
                    result["generated_images"] = generated_images
                result["output_path"] = str(output)
                from ...storage.storage_service import get_storage_service
                storage = get_storage_service()
                file_info = storage.save_new_output(
                    source_path=str(output),
                    original_name=_make_registered_name(input_path, str(output), options),
                    change_description=instruction if instruction else "PPT generation",
                )
                if not file_info or not file_info.file_id:
                    raise RuntimeError("output registration failed: no file_id")
                result["output_files"].append(file_info.file_id)
            result["slides"] = slides or 0
            if message:
                result["message"] = message

            # The content planner can make several model calls.  Report the
            # actual content-generation call, not only the preliminary
            # follow-up rewriter call.
            if getattr(model_gateway, "last_call", None):
                result["model_call"] = model_gateway.last_call

        if progress:
            progress.update(100, "PPT生成完成")

        logger.info(f"PPT任务 {_task_id} 完成: {result.get('output_path', '无输出')}")

    except Exception as e:
        logger.error("PPT任务 %s 失败: %s", _task_id, sanitize_error(e))
        result["status"] = "failed"
        from ...security.error_sanitizer import sanitize_error
        result["error"] = sanitize_error(e)
        if progress:
            progress.update(100, f"处理失败: {e}")

    if options.get("model_call"):
        result["model_call"] = options["model_call"]
    return result


def design_ppt(content: str, output_path: str = None,
               style: str = "modern", options: dict = None,
               progress=None, _task_id: str = None, **kwargs) -> dict:
    """PPT 设计任务（已有内容，只做设计）"""
    return generate_ppt(
        outline=content,
        output_path=output_path,
        template_type=style,
        options=options or {},
        progress=progress,
        _task_id=_task_id,
    )
