"""
对话路由 - 自然语言入口
"""
import uuid
import json
import logging
import os
from fastapi import APIRouter, HTTPException

from ..schemas.request import ChatRequest
from ..schemas.response import ChatResponse, BaseResponse
from ..routing import route_intent, route_by_file_path

router = APIRouter(prefix="/api", tags=["对话"])

logger = logging.getLogger("office_agent.api.chat")


def _resolve_input_files(file_ids, file_repo):
    """Resolve Storage file IDs to verified local paths before task creation."""
    from ...storage.storage_service import get_storage_service

    storage = get_storage_service()
    input_paths = []
    for file_id in file_ids:
        db_file = file_repo.get_by_id(file_id)
        if not db_file or db_file.status == "deleted":
            raise HTTPException(status_code=404, detail=f"文件不存在或已删除: {file_id}")
        try:
            local_path = storage.get_file_path(file_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"文件不存在或已删除: {file_id}") from exc
        if not local_path or not os.path.isfile(local_path):
            raise HTTPException(status_code=422, detail=f"文件存储内容不可用: {file_id}")
        input_paths.append(local_path)
        logger.info("已解析任务输入文件: file_id=%s", file_id)
    return input_paths


def _route_intent(message: str) -> tuple:
    """
    简单意图路由（关键词匹配）
    返回 (agent, task_type, intent)
    """
    # Compatibility shim: routing logic lives in office_agent.api.routing.
    return route_intent(message)

    # Word相关
    word_keywords = ["word", "文档", "排版", "格式", "论文", "公文", "报告", "docx", "doc"]
    if any(k in msg for k in word_keywords):
        return "word_agent", "word_format", "format"

    # PPT相关
    ppt_keywords = ["ppt", "pptx", "演示", "幻灯片", "汇报", "幻灯", "课件"]
    if any(k in msg for k in ppt_keywords):
        return "ppt_agent", "ppt_generate", "generate"

    # Excel相关
    excel_keywords = ["excel", "xlsx", "xls", "表格", "数据", "分析", "图表", "统计"]
    if any(k in msg for k in excel_keywords):
        return "excel_agent", "excel_analyze", "analyze"

    return "orchestrator", "general", "general"


def _task_type_to_queue_name(task_type: str) -> str:
    """任务类型映射到队列任务名"""
    mapping = {
        "word_format": "word.format",
        "word_process": "word.process",
        "word_convert": "word.convert",
        "ppt_generate": "ppt.generate",
        "ppt_process": "ppt.process",
        "ppt_design": "ppt.design",
        "excel_analyze": "excel.analyze",
        "excel_chart": "excel.chart",
        "excel_process": "excel.process",
        "file_convert": "file.convert",
        "file_process": "file.process_upload",
        "general": "general.process",
    }
    return mapping.get(task_type, task_type)


def _route_by_file_path(path: str):
    """Route follow-up messages using the attached document type."""
    # Compatibility shim: routing logic lives in office_agent.api.routing.
    return route_by_file_path(path)
    if ext in (".docx", ".doc"):
        return "word_agent", "word_process", "file_type"
    if ext in (".pptx", ".ppt"):
        return "ppt_agent", "ppt_generate", "file_type"
    if ext in (".xlsx", ".xls", ".csv"):
        return "excel_agent", "excel_analyze", "file_type"
    return None


def _recover_conversation_context(conversation_id: str, task_repo, storage):
    """Recover the latest artifact and routing context when the client omits it."""
    if not conversation_id:
        return None
    for task in task_repo.get_recent(limit=100):
        try:
            options = json.loads(task.options_json or "{}")
        except (TypeError, ValueError):
            continue
        if options.get("conversation_id") != conversation_id:
            continue
        output_ids = []
        try:
            output_ids = json.loads(task.output_file_ids or "[]")
        except (TypeError, ValueError):
            pass
        for file_id in output_ids:
            try:
                path = storage.get_file_path(file_id)
            except (FileNotFoundError, TypeError):
                continue
            if path and os.path.isfile(path):
                return {
                    "task": task,
                    "options": options,
                    "input_paths": [path],
                    "previous_instruction": task.instruction,
                }
        paths = options.get("input_paths") or []
        if paths and os.path.isfile(paths[0]):
            return {
                "task": task,
                "options": options,
                "input_paths": [paths[0]],
                "previous_instruction": task.instruction,
            }
    return None


def _sanitize_model_config(model_config):
    """清洗前端模型配置：只保留 provider/model，剥离 api_key 等敏感字段。"""
    if not isinstance(model_config, dict):
        return None
    clean = {}
    for key in ("provider", "model"):
        value = model_config.get(key)
        if isinstance(value, str) and value.strip():
            clean[key] = value.strip()
    return clean or None


def _sanitize_history(history):
    """Keep a small, text-only conversation window for follow-up instructions."""
    if not isinstance(history, list):
        return []
    clean = []
    for item in history[-10:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            clean.append({"role": role, "content": content.strip()[:4000]})
    return clean


def _classify_follow_up(message: str, is_follow_up: bool) -> str:
    """Classify the user's revision intent without replacing LLM planning."""
    if not is_follow_up:
        return "new_task"
    text = (message or "").lower()
    if any(k in text for k in ("恢复之前", "回到之前", "撤销", "还原", "undo", "revert")):
        return "revert"
    if any(k in text for k in ("不要", "去掉", "删除", "取消", "remove", "delete")):
        return "remove"
    if any(k in text for k in ("改", "修改", "调整", "不对", "还是", "change", "fix")):
        return "modify"
    return "add"


@router.post("/chat", response_model=BaseResponse[ChatResponse], summary="对话入口")
async def chat(req: ChatRequest):
    """
    接收用户自然语言需求，自动识别意图并创建任务。
    走真正的数据库+任务队列，而非内存模拟。
    """
    from ...database.session import session_scope
    from ...database.repository import TaskRepository, FileRepository

    # 兼容前端发送的不同字段名
    agent_hint = req.agent_hint
    file_ids = req.file_ids or []

    # 意图识别
    if agent_hint and agent_hint != "auto":
        agent = agent_hint if agent_hint.endswith("_agent") else f"{agent_hint}_agent"
        task_type = f"{agent_hint}_task"
        intent = "manual"
        # 根据agent推断task_type
        if "word" in agent:
            task_type = "word_process"
        elif "ppt" in agent:
            task_type = "ppt_generate"
        elif "excel" in agent:
            task_type = "excel_analyze"
    else:
        agent, task_type, intent = route_intent(req.message)

    # 1. 创建数据库记录并获取文件路径
    task_id = None
    input_paths = []
    with session_scope() as session:
        task_repo = TaskRepository(session)
        file_repo = FileRepository(session)

        recovered = None
        if not file_ids and req.conversation_id:
            from ...storage.storage_service import get_storage_service
            recovered = _recover_conversation_context(
                req.conversation_id, task_repo, get_storage_service()
            )
            if recovered:
                file_ids = []
                input_paths = recovered["input_paths"]
                # A follow-up inherits the prior agent/task route unless the
                # user explicitly selected another agent.
                if not agent_hint or agent_hint == "auto":
                    agent = recovered["task"].agent_name or agent
                    task_type = recovered["task"].task_type
                    intent = "follow_up"

        if file_ids:
            input_paths = _resolve_input_files(file_ids, file_repo)
            # A follow-up such as "标题间距不对" often contains no file-type
            # keyword. The uploaded/previous output file is authoritative.
            if task_type == "general" and input_paths:
                routed = route_by_file_path(input_paths[0])
                if routed:
                    agent, task_type, intent = routed

        # 从context中提取模型配置（仅保留非敏感字段，防止 API Key 进入任务数据）
        model_config = _sanitize_model_config(req.context.get("model_config") if req.context else None)
        history = _sanitize_history(req.context.get("history") if req.context else None)
        revision_mode = _classify_follow_up(req.message, bool(recovered))
        revision_number = (recovered["task"].revision_number + 1) if recovered else 1
        if recovered:
            previous_history = _sanitize_history(recovered["options"].get("history"))
            # Keep the prior objective explicit even when the client only sends
            # assistant status text or has lost its local message history.
            prior_turn = {
                "role": "user",
                "content": recovered.get("previous_instruction", ""),
            }
            history = (previous_history + [prior_turn] + history)[-10:]

        db_task = task_repo.create_task(
            task_type=task_type,
            instruction=req.message,
            agent_name=agent,
            input_file_ids=json.dumps(file_ids) if file_ids else None,
            options_json=json.dumps({
                "intent": intent,
                "conversation_id": req.conversation_id,
                "input_paths": input_paths,
                "model_config": model_config,
                "history": history,
                "is_follow_up": bool(recovered),
                "parent_task_id": recovered["task"].id if recovered else None,
                "previous_instruction": recovered.get("previous_instruction") if recovered else None,
                "revision_mode": revision_mode,
                "revision_number": revision_number,
            }, ensure_ascii=False),
            priority=1,
            parent_task_id=recovered["task"].id if recovered else None,
            revision_number=revision_number,
        )
        task_id = db_task.id

    # 2. 提交到真正的任务队列
    status = "queued"
    try:
        from ...task_queue import submit_task, init_worker, TASK_REGISTRY
        init_worker()

        queue_task_name = _task_type_to_queue_name(task_type)
        submit_task(
            task_name=queue_task_name,
            kwargs={
                "input_path": input_paths[0] if input_paths else None,
                "input_paths": input_paths,
                "instruction": req.message,
                "options": {
                    "intent": intent,
                    "model_config": model_config,
                    "input_file_ids": file_ids,
                    "input_paths": input_paths,
                    "history": history,
                    "is_follow_up": bool(recovered),
                    "parent_task_id": recovered["task"].id if recovered else None,
                    "previous_instruction": recovered.get("previous_instruction") if recovered else None,
                    "revision_mode": revision_mode,
                },
            },
            priority="normal",
            task_id=task_id,
            task_type=task_type,
            instruction=req.message,
        )
        logger.info(f"聊天任务已提交到队列: {task_id} -> {queue_task_name} (agent={agent})")
    except Exception as e:
        logger.error(f"任务队列提交失败: {e}", exc_info=True)
        with session_scope() as session:
            TaskRepository(session).fail_task(task_id, f"任务队列提交失败: {e}")
        status = "failed"

    data = ChatResponse(
        task_id=task_id,
        conversation_id=req.conversation_id or str(uuid.uuid4()),
        agent=agent,
        intent=intent,
        status=status,
        message=f"已为您分配 {agent} 处理",
        estimated_time=30,
        is_follow_up=bool(recovered),
        parent_task_id=recovered["task"].id if recovered else None,
        revision_mode=revision_mode,
        revision_number=revision_number,
    )
    return BaseResponse(data=data)
