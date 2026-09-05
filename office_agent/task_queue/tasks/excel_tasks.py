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
import uuid
import logging
import json
from ...security.error_sanitizer import sanitize_error
from ...runtime_config import get_output_dir

logger = logging.getLogger("office_agent.tasks.excel")

def _understand_excel_request(instruction: str, options: dict,
                              progress=None) -> str:
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
        response = ModelGateway(
            cancel_event=getattr(progress, "cancel_event", None)
        ).chat(
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
                                      "fallback_used": True, "error": sanitize_error(exc, "模型解析不可用"), "attempts": 0}
        logger.warning("Excel LLM理解不可用，使用原始指令: %s", exc)
    return instruction


def _safe_output(ext: str = ".xlsx") -> str:
    output_dir = str(get_output_dir())
    os.makedirs(output_dir, exist_ok=True)
    # 时间戳只精确到秒，normal 队列并发下同秒完成的任务会写同一路径互相覆盖
    unique = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    return os.path.join(output_dir, f"excel_{unique}{ext}")


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


CSV_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "big5", "latin-1")


def _read_csv_any_encoding(csv_path: str):
    """按常见编码链读取 CSV（中文 Excel 导出的 GBK CSV 最常见），BOM 自动剥离"""
    import pandas as pd
    last_err = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(csv_path, encoding=enc)
        except (UnicodeDecodeError, UnicodeError) as exc:
            last_err = exc
            continue
    raise RuntimeError(f"无法识别 CSV 文件编码: {last_err}")


def _sanitize_csv_cell(value):
    """CSV 公式注入防护：以 = + - @ 开头的文本单元格前缀单引号，
    防止被 openpyxl 当作公式（含 DDE）写入输出文件。"""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        # 纯数字的负号（如 -12.5）不是注入，保留数值语义
        rest = value[1:]
        try:
            float(rest)
            return value
        except ValueError:
            return "'" + value
    return value


def _csv_to_xlsx(csv_path: str) -> str:
    """CSV → 临时 xlsx，供统一 openpyxl 处理链使用"""
    import pandas as pd
    df = _read_csv_any_encoding(csv_path)
    df = df.where(pd.notnull(df), None)
    df = df.map(_sanitize_csv_cell) if hasattr(df, "map") else df.applymap(_sanitize_csv_cell)
    output_dir = str(get_output_dir())
    tmp = os.path.join(output_dir, f"csv_{int(time.time() * 1000)}_{os.getpid()}.xlsx")
    os.makedirs(output_dir, exist_ok=True)
    df.to_excel(tmp, index=False, sheet_name="Sheet1", engine="openpyxl")
    return tmp


def analyze_excel(input_path: str, output_path: str | None = None,
                  instruction: str = "", options: dict | None = None,
                  progress=None, _task_id: str | None = None, **kwargs) -> dict:
    """
    Excel 数据分析任务
    """
    options = options or {}
    result: dict = {"status": "success", "output_files": [], "analysis": {}}

    try:
        if progress:
            progress.update(5, "初始化Excel分析")

        if not os.path.exists(input_path):
            raise FileNotFoundError(f"文件不存在: {input_path}")

        # CSV 转临时 xlsx；.xls（Excel 97-2003）openpyxl 不支持，明确拒绝
        original_input_path = input_path
        ext = os.path.splitext(input_path)[1].lower()
        if ext == ".xls":
            raise RuntimeError("不支持 .xls（Excel 97-2003）格式，请另存为 .xlsx 后重试")
        csv_temp_path = None
        if ext == ".csv":
            input_path = _csv_to_xlsx(input_path)
            csv_temp_path = input_path

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

        try:
            # 调用真实存在的 Excel 处理入口：输入 xlsx -> 分析/公式/图表/格式化 -> 输出 xlsx
            effective_instruction = _understand_excel_request(
                instruction, options, progress
            )
            if progress:
                progress.check_cancelled()
            process_result = orchestrator.process_file(
                file_path=input_path,
                task=effective_instruction,
                output_path=output_path,
                chart_type=(options.get("chart_type") or ""),
            )

            if not process_result or not process_result.success:
                message = (getattr(process_result, "message", None) or "Excel处理失败")
                raise RuntimeError(f"Excel处理失败: {message}")

            output = process_result.output_path
            if not output or not os.path.exists(str(output)):
                raise RuntimeError(f"Excel引擎未生成输出文件: {output}")
        finally:
            # 无论成功失败都清理 CSV 转换产生的临时 xlsx，防止失败路径泄漏
            if csv_temp_path and os.path.exists(csv_temp_path):
                try:
                    os.remove(csv_temp_path)
                except OSError:
                    pass

        if progress:
            progress.update(80, "登记输出文件到 Storage")

        # 输出以独立新文件登记（outputs 桶，新 file_id，不触发版本覆盖）
        from ...storage.storage_service import get_storage_service

        storage = get_storage_service()
        original_name = options.get("output_filename") or (
            f"{_display_stem(original_input_path, options)}_processed.xlsx"
        )
        file_info = storage.save_new_output(
            source_path=str(output),
            original_name=original_name,
            owner_id=options.get("_owner_id"),
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
        result["error"] = sanitize_error(e)

    if options.get("model_call"):
        result["model_call"] = options["model_call"]
    return result


def generate_chart(input_path: str, output_path: str | None = None,
                   chart_type: str = "bar", options: dict | None = None,
                   progress=None, _task_id: str | None = None, **kwargs) -> dict:
    """图表生成任务"""
    return analyze_excel(
        input_path=input_path,
        output_path=output_path,
        instruction=f"生成{chart_type}图表",
        options={"chart_type": chart_type, **(options or {})},
        progress=progress,
        _task_id=_task_id,
    )
