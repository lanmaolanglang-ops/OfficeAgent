"""
Excel 相关后台任务

任务流程：
    0%   初始化
    20%  数据读取
    40%  数据分析
    60%  公式/图表生成
    80%  结果写入
    100% 完成
"""
import os
import time
import logging
import traceback
import json
from ...security.error_sanitizer import sanitize_error

logger = logging.getLogger("office_agent.tasks.excel")

OUTPUT_DIR = os.path.expanduser("~/.office_agent/outputs")


def _understand_excel_request(instruction: str, options: dict) -> str:
    """Normalize conversational follow-ups while retaining the original request."""
    if not instruction:
        return instruction
    try:
        from ...model_gateway import ModelGateway
        history = options.get("history", [])[-8:] if isinstance(options, dict) else []
        prompt = json.dumps({
            "request": instruction,
            "previous_instruction": options.get("previous_instruction", ""),
            "is_follow_up": bool(options.get("is_follow_up")),
            "history": history,
        }, ensure_ascii=False)
        response = ModelGateway().chat(
            user_message=prompt,
            system_prompt=("你是Excel任务解析器。将用户要求改写成一条明确、可执行的Excel操作指令。"
                           "保留原始字段、工作表、范围、公式、排序、筛选、图表和格式要求。"
                           "只输出改写后的中文指令，不要解释。"),
            task_type_str="simple_text", temperature=0.1, max_tokens=800,
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
        logger.warning("Excel LLM理解不可用，使用原始指令: %s", exc)
    return instruction


def _safe_output(ext: str = ".xlsx") -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, f"excel_{time.strftime('%Y%m%d_%H%M%S')}{ext}")


def analyze_excel(input_path: str, output_path: str = None,
                  instruction: str = "", options: dict = None,
                  progress=None, _task_id: str = None, **kwargs) -> dict:
    """
    Excel 数据分析任务
    """
    options = options or {}
    result = {"status": "success", "output_files": [], "analysis": {}}

    try:
        if progress:
            progress.update(5, "初始化Excel分析")

        if not os.path.exists(input_path):
            raise FileNotFoundError(f"文件不存在: {input_path}")

        if not output_path:
            output_path = _safe_output()

        if progress:
            progress.update(20, "读取Excel数据")

        from ...excel_agent.excel_orchestrator import ExcelOrchestrator

        orchestrator = ExcelOrchestrator()

        if progress:
            progress.update(40, "分析数据结构")

        if progress:
            progress.update(60, "执行 Excel 处理")

        # 调用真实存在的 Excel 处理入口：输入 xlsx -> 分析/公式/图表/格式化 -> 输出 xlsx
        effective_instruction = _understand_excel_request(instruction, options)
        process_result = orchestrator.process_file(
            file_path=input_path,
            task=effective_instruction,
            output_path=output_path,
        )

        if not process_result or not process_result.success:
            message = (getattr(process_result, "message", None) or "Excel处理失败")
            raise RuntimeError(f"Excel处理失败: {message}")

        output = process_result.output_path
        if not output or not os.path.exists(str(output)):
            raise RuntimeError(f"Excel引擎未生成输出文件: {output}")

        if progress:
            progress.update(80, "登记输出文件到 Storage")

        # 输出以独立新文件登记（outputs 桶，新 file_id，不触发版本覆盖）
        from ...storage.storage_service import get_storage_service

        storage = get_storage_service()
        original_name = options.get("output_filename") or (
            f"{os.path.splitext(os.path.basename(input_path))[0]}_processed.xlsx"
        )
        file_info = storage.save_new_output(
            source_path=str(output),
            original_name=original_name,
            change_description=instruction or "Excel 文档处理",
        )

        if not file_info or not file_info.file_id:
            raise RuntimeError("输出文件登记失败: 未获得 file_id")

        result["output_files"].append(file_info.file_id)
        result["output_path"] = str(output)
        result["output_file_id"] = file_info.file_id
        result["message"] = getattr(process_result, "message", "Excel处理完成")
        result["analysis"] = {
            "quality_score": getattr(process_result, "quality_score", None),
            "changes": getattr(process_result, "changes", []),
        }

        if progress:
            progress.update(100, "Excel处理完成")

        logger.info(f"Excel任务 {_task_id} 完成: file_id={file_info.file_id}")

    except Exception as e:
        logger.error("Excel任务 %s 失败: %s", _task_id, sanitize_error(e))
        result["status"] = "failed"
        from ...security.error_sanitizer import sanitize_error
        result["error"] = sanitize_error(e)
        raise

    if options.get("model_call"):
        result["model_call"] = options["model_call"]
    return result


def generate_chart(input_path: str, output_path: str = None,
                   chart_type: str = "bar", options: dict = None,
                   progress=None, _task_id: str = None, **kwargs) -> dict:
    """图表生成任务"""
    return analyze_excel(
        input_path=input_path,
        output_path=output_path,
        instruction=f"生成{chart_type}图表",
        options={"chart_type": chart_type, **(options or {})},
        progress=progress,
        _task_id=_task_id,
    )


def process_data(input_path: str, output_path: str = None,
                 operations: list = None, options: dict = None,
                 progress=None, _task_id: str = None, **kwargs) -> dict:
    """数据处理任务（清洗、转换、合并等）"""
    return analyze_excel(
        input_path=input_path,
        output_path=output_path,
        instruction="数据处理: " + ", ".join(operations or []),
        options={"operations": operations or [], **(options or {})},
        progress=progress,
        _task_id=_task_id,
    )
