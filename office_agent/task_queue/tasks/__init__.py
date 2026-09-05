"""
任务定义

所有任务函数统一签名：
    def task_func(..., progress=None, _task_id=None, **kwargs):
        progress.update(20, "步骤描述")
        return {"status": "success", ...}

任务不直接操作数据库，通过 Service 层调用 Agent。
"""
import logging
from typing import Callable
from ...api.routing import resolve_route
from .word_tasks import process_word, format_document
from .ppt_tasks import generate_ppt, design_ppt
from .excel_tasks import analyze_excel, generate_chart
from .file_tasks import (
    process_upload, convert_format, cleanup_temp_files, system_health_check,
    cleanup_old_logs,
)
from .rag_tasks import (
    index_document, chunk_and_embed, refresh_knowledge_base, search_knowledge,
)

logger = logging.getLogger("office_agent.tasks.general")


def process_general(instruction: str = "", input_path: str | None = None,
                    options: dict | None = None, progress=None, _task_id: str | None = None,
                    **kwargs) -> dict:
    """
    通用任务处理 - 由 orchestrator 路由
    根据指令内容分发到具体的 Word/PPT/Excel 处理器
    """
    options = options or {}
    result = {"status": "success", "output_files": [], "message": ""}

    try:
        if progress:
            progress.update(5, "理解任务需求")

        # 与 chat 层同一权威路由裁决（resolve_route 内部：文件类型优先）
        agent, _, _ = resolve_route(instruction, input_path)

        if agent == "ppt_agent":
            if progress:
                progress.update(20, "正在生成PPT...")
            return generate_ppt(
                outline=instruction,
                input_path=input_path,
                options=options,
                progress=progress,
                _task_id=_task_id,
            )
        elif agent == "word_agent":
            if not input_path:
                if progress:
                    progress.update(100, "请先上传文件")
                result["status"] = "failed"
                result["error"] = "Word处理需要先上传文档文件"
                return result
            if progress:
                progress.update(20, "正在处理Word文档...")
            return process_word(
                input_path=input_path,
                instruction=instruction,
                options=options,
                progress=progress,
                _task_id=_task_id,
            )
        elif agent == "excel_agent":
            if not input_path:
                if progress:
                    progress.update(100, "请先上传文件")
                result["status"] = "failed"
                result["error"] = "Excel处理需要先上传表格文件"
                return result
            if progress:
                progress.update(20, "正在分析Excel...")
            return analyze_excel(
                input_path=input_path,
                instruction=instruction,
                options=options,
                progress=progress,
                _task_id=_task_id,
            )
        else:
            # 无法路由时必须显式失败，不能把空结果冒充成功。
            result["status"] = "failed"
            result["error"] = "无法识别或不支持的任务类型"
            result["message"] = "请明确指定 Word、PPT、Excel 或 RAG 任务"
            logger.warning("通用任务 %s 无法路由: %s", _task_id, instruction[:100])

    except Exception as e:
        logger.error(f"通用任务 {_task_id} 失败: {e}")
        result["status"] = "failed"
        from ...security.error_sanitizer import sanitize_error
        result["error"] = sanitize_error(e)
        if progress:
            progress.update(100, f"处理失败: {e}")

    return result


# 任务注册表（供 LocalWorker 使用）
TASK_REGISTRY: dict[str, Callable] = {
    # General
    "general.process": process_general,
    # Word
    "word.process": process_word,
    "word.format": format_document,
    # PPT
    "ppt.generate": generate_ppt,
    "ppt.design": design_ppt,
    # Excel
    "excel.analyze": analyze_excel,
    "excel.chart": generate_chart,
    # File
    "file.process_upload": process_upload,
    "file.convert": convert_format,
    "file.cleanup": cleanup_temp_files,
    "file.health_check": system_health_check,
    "file.cleanup_logs": cleanup_old_logs,
    # RAG
    "rag.index": index_document,
    "rag.embed": chunk_and_embed,
    "rag.refresh": refresh_knowledge_base,
    "rag.search": search_knowledge,
}

# 任务类型 → 队列任务名（唯一权威映射，必须与 TASK_REGISTRY 键对齐）
TASK_TYPE_TO_QUEUE = {
    "word_format": "word.format",
    "word_process": "word.process",
    "ppt_generate": "ppt.generate",
    "ppt_process": "ppt.generate",   # 历史别名：无独立 ppt.process 处理器，归并到 ppt.generate
    "ppt_design": "ppt.design",
    "excel_analyze": "excel.analyze",
    "excel_chart": "excel.chart",
    "file_convert": "file.convert",
    "file_process": "file.process_upload",
    "general": "general.process",
    "rag_index": "rag.index",
    "rag_search": "rag.search",
}


def queue_name_for_task_type(task_type: str) -> str:
    """任务类型 → 注册队列名；未知类型原样返回（由 worker 兜底报错）。"""
    return TASK_TYPE_TO_QUEUE.get(task_type, task_type)


__all__ = ["TASK_REGISTRY", "TASK_TYPE_TO_QUEUE", "queue_name_for_task_type"]
