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
import uuid
import logging
import json
from ...security.error_sanitizer import sanitize_error
from pathlib import Path

logger = logging.getLogger("office_agent.tasks.ppt")

# PPT 页数上限（防止异常指令导致生成过多页）
MAX_SLIDES = 50
DEFAULT_SLIDES = 10


def _parse_slide_count(instruction: str, effective: str) -> int:
    """从用户指令/简报中解析期望页数，默认 10，限制 [1, 50]"""
    text = f"{instruction or ''} {effective or ''}"
    m = re.search(r'(\d{1,3})\s*页', text)
    if m:
        return max(1, min(MAX_SLIDES, int(m.group(1))))
    return DEFAULT_SLIDES


def _image_failure_summary(error: str) -> tuple[str, str]:
    """Map provider errors to stable user-facing codes and recovery copy."""
    text = str(error or "")
    lowered = text.lower()
    if "http 401" in lowered or "http 403" in lowered:
        return "auth", "生图服务鉴权失败，请在设置中更新 API Key 并测试生图"
    if "http 429" in lowered or "rate limit" in lowered or "quota" in lowered:
        return "quota", "生图服务额度不足或请求受限，请稍后重试"
    if "http 400" in lowered:
        return "request", "生图请求被拒绝，请检查模型名称和参数"
    if "http 404" in lowered:
        return "not_found", "未找到生图端点或模型，请检查 Base URL 和模型名称"
    if "getaddrinfo" in lowered or "name resolution" in lowered or "连接失败" in text:
        return "connection", "无法连接生图服务，请检查 Base URL 和网络"
    if "timeout" in lowered or "timed out" in lowered or "超时" in text:
        return "timeout", "生图请求超时，请检查网络后重试"
    return "unknown", "生图失败，请在设置中测试生图配置"


# 输出目录
OUTPUT_DIR = os.path.join(
    os.environ.get("OFFICE_AGENT_DATA_DIR") or os.path.expanduser("~/.office_agent"),
    "outputs")


def _display_stem(input_path: str, options: dict) -> str:
    """输出文件名应基于用户上传时的原始文件名，而非内部 file_id 路径。"""
    stem = os.path.splitext(os.path.basename(input_path or ""))[0]
    input_ids = (options or {}).get("input_file_ids") or []
    if input_ids:
        try:
            from ...storage.storage_service import get_storage_service
            original = get_storage_service().get_info(input_ids[0]).original_name or ""
            original_stem = os.path.splitext(original)[0]
            if original_stem and not original_stem.startswith("file_"):
                stem = original_stem
        except Exception:
            pass
    return stem or "output"


def _make_registered_name(input_path: str, output_path: str, options: dict) -> str:
    """Build the registered output filename for Storage (does not change task-internal naming)."""
    if options and options.get("output_filename"):
        return str(options["output_filename"])
    if input_path:
        stem = _display_stem(input_path, options)
        if stem:
            return f"{stem}_presentation.pptx"
    if output_path:
        return Path(output_path).name or "presentation.pptx"
    return "presentation.pptx"


def _safe_filename(text: str, ext: str = ".pptx") -> str:
    """生成安全的文件名，避免中文/特殊字符问题"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # 时间戳只精确到秒，并发任务同秒完成会写同一路径互相覆盖
    unique = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    return os.path.join(OUTPUT_DIR, f"ppt_{unique}{ext}")


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
                                      "fallback_used": True, "error": sanitize_error(exc, "模型解析不可用"), "attempts": 0}
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

        # 调用 PPT Orchestrator（传入ModelGateway以启用AI内容生成；模型选择由“默认模型”机制接管）
        from ...ppt_agent.ppt_orchestrator import PPTOrchestrator
        try:
            from ...model_gateway import ModelGateway
            model_gateway = ModelGateway()
            logger.info("ModelGateway已初始化，将使用AI生成PPT内容")
        except Exception as e:
            logger.warning(f"ModelGateway初始化失败，将使用模板: {e}")
            model_gateway = None
        # 生图网关：读取用户配置的生图模型，用于按需配图
        image_gateway = None
        try:
            from ...image_generation import ImageGenerationGateway
            from ...image_generation.config import get_image_model_config
            icfg = options.get("image_model_config") or get_image_model_config()
            image_gateway = ImageGenerationGateway(
                api_key=icfg.get("api_key", ""),
                base_url=icfg.get("base_url", ""),
                model=icfg.get("model", ""),
                provider=icfg.get("provider", ""),
                mcp_url=icfg.get("mcp_url", ""),
            )
        except Exception as e:
            logger.warning(f"生图网关初始化失败: {e}")
        try:
            max_generated_images = max(0, min(int(options.get("max_generated_images", 3)), 8))
        except (TypeError, ValueError):
            max_generated_images = 3
        if options.get("generate_images", True) is False:
            max_generated_images = 0
        orchestrator = PPTOrchestrator(
            model_gateway=model_gateway,
            image_gateway=image_gateway,
            max_generated_images=max_generated_images,
        )

        # 确定主题和输出路径
        effective_instruction = _understand_ppt_request(instruction or outline or "", options)
        # The LLM-resolved brief must take precedence during follow-ups.
        theme = effective_instruction or outline or "演示文稿"
        style = template_type or "professional"
        # 从用户指令解析期望页数（默认 10，最大 50）
        slide_count = _parse_slide_count(instruction or "", effective_instruction)
        if not output_path:
            output_path = _safe_filename(theme)

        if progress:
            progress.update(20, "规划PPT内容结构")

        if progress:
            progress.update(40, f"选择{style}风格模板")

        # 根据输入类型选择生成方法
        ppt_result = None

        # 用户指定了 PPT 模板 → 分析模板并按模板配色/字体/版式生成
        template_path = (options or {}).get("template_path")
        if template_path and os.path.exists(template_path):
            if progress:
                progress.update(60, "分析模板并按模板生成PPT...")
            ppt_result = orchestrator.generate_with_template(
                template_path=template_path,
                theme=theme,
                slide_count=slide_count,
                output_path=output_path,
            )
        elif input_path and os.path.exists(input_path):
            if progress:
                progress.update(60, "从文件生成PPT...")
            ext = Path(input_path).suffix.lower()
            if ext == '.doc':
                result["status"] = "failed"
                result["error"] = "不支持 .doc（Word 97-2003）格式，请先用 Word/WPS 另存为 .docx 后重试"
                if progress:
                    progress.update(100, "处理失败")
                return result
            if ext in ('.docx',):
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
                slide_count=slide_count,
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
                image_status = dict(orchestrator.image_generation)
                errors = image_status.pop("errors", [])
                generated_images = int(image_status.get("generated", 0) or 0)
                if max_generated_images <= 0:
                    image_status.update(status="disabled", message="已关闭自动配图")
                elif not image_status.get("configured"):
                    image_status.update(status="unconfigured", message="未配置可用的生图服务")
                elif generated_images:
                    state = "partial" if errors else "success"
                    image_status.update(
                        status=state,
                        message=(f"已生成 {generated_images} 张配图"
                                 if not errors else f"已生成 {generated_images} 张配图，部分页面配图失败"),
                    )
                    if errors:
                        code, warning = _image_failure_summary(str(errors[0]))
                        image_status["error_code"] = code
                        result["warnings"] = [warning]
                elif errors:
                    code, message_text = _image_failure_summary(str(errors[0]))
                    image_status.update(status="failed", error_code=code, message=message_text)
                    result["warnings"] = [message_text]
                else:
                    image_status.update(status="skipped", message="当前内容没有适合自动配图的页面")
                image_status["requested"] = max_generated_images
                result["image_generation"] = image_status
                if generated_images:
                    result["generated_images"] = generated_images
                result["output_path"] = str(output)
                from ...storage.storage_service import get_storage_service
                storage = get_storage_service()
                file_info = storage.save_new_output(
                    source_path=str(output),
                    original_name=_make_registered_name(input_path, str(output), options),
                    owner_id=options.get("_owner_id"),
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
            last_call = getattr(model_gateway, "last_call", None)
            if last_call:
                result["model_call"] = last_call
                if not last_call.get("success", True):
                    # LLM 调用失败时已回退模板，明确告知用户
                    result["message"] = (message or "PPT已生成") + "（模型调用失败，已使用模板内容）"

        if progress:
            progress.update(100, "PPT生成完成")

        logger.info(f"PPT任务 {_task_id} 完成: {result.get('output_path', '无输出')}")

    except Exception as e:
        logger.error("PPT任务 %s 失败: %s", _task_id, sanitize_error(e))
        result["status"] = "failed"
        result["error"] = sanitize_error(e)
        if progress:
            progress.update(100, f"处理失败: {result['error']}")

    # 内容生成层的调用（last_call）优先；仅当未生成时回退到改写层的记录
    if "model_call" not in result and options.get("model_call"):
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
