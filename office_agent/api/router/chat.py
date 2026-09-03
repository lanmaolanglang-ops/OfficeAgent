"""
对话路由 - 自然语言入口
"""
import uuid
import json
import logging
import os
from fastapi import APIRouter, HTTPException, Request

from ..schemas.request import ChatRequest
from ..schemas.response import ChatResponse, BaseResponse
from ..routing import resolve_route, route_intent
from ..core.config import settings
from ..core.file_resolution import resolve_input_files

router = APIRouter(prefix="/api", tags=["对话"])

logger = logging.getLogger("office_agent.api.chat")

_prompt_scanner = None


def _scan_user_prompt(message: str, request: Request):
    """Enforce prompt policy before routing or writing any task state."""
    global _prompt_scanner
    from ...security.prompt import PromptAction, PromptSecurityScanner

    if _prompt_scanner is None:
        _prompt_scanner = PromptSecurityScanner()
    result = _prompt_scanner.scan(message, source="user")
    if result.action == PromptAction.REJECT:
        from ...security.audit import get_audit_logger

        get_audit_logger().log_prompt_injection(
            user_id=getattr(request.state, "user_id", "anonymous"),
            matches=result.matches,
            ip=request.client.host if request.client else None,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PROMPT_POLICY_REJECTED",
                "message": "输入包含高风险指令操纵或模型控制标记",
                "risk_level": result.risk_level,
            },
        )
    return result


def _recover_conversation_context(conversation_id: str, task_repo, storage,
                                  owner_id: str | None = None):
    """Recover the latest artifact and routing context when the client omits it."""
    if not conversation_id:
        return None
    offset = 0
    while True:
        try:
            tasks = task_repo.get_recent(limit=200, offset=offset)
        except TypeError:
            if offset:
                break
            tasks = task_repo.get_recent(limit=1000)
        if not tasks:
            break
        offset += len(tasks)
        for task in tasks:
            if owner_id and getattr(task, "user_id", None) != owner_id:
                continue
            try:
                options = json.loads(task.options_json or "{}")
            except (TypeError, ValueError):
                continue
            if options.get("conversation_id") != conversation_id:
                continue
            try:
                output_ids = json.loads(task.output_file_ids or "[]")
            except (TypeError, ValueError):
                output_ids = []
            output_paths = []
            for file_id in output_ids:
                try:
                    path = storage.get_file_path(file_id)
                except (FileNotFoundError, TypeError):
                    continue
                if path and os.path.isfile(path):
                    output_paths.append(path)
            if output_paths:
                return {
                    "task": task,
                    "options": options,
                    "input_paths": output_paths,
                    "previous_instruction": task.instruction,
                }
            paths = [path for path in (options.get("input_paths") or [])
                     if path and os.path.isfile(path)]
            if paths:
                return {
                    "task": task,
                    "options": options,
                    "input_paths": paths,
                    "previous_instruction": task.instruction,
                }
    return None


def _request_identity(request: Request) -> tuple[str | None, str]:
    if not settings.auth_enabled:
        return None, ""
    user_id = getattr(request.state, "user_id", None)
    role = getattr(request.state, "user_role", "")
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="缺少已认证用户")
    return user_id, role


def _require_owned_files(file_ids, file_repo, user_id: str | None, role: str):
    if not user_id or role == "admin":
        return
    for file_id in file_ids:
        db_file = file_repo.get_by_id(file_id)
        if not db_file or db_file.owner_id != user_id:
            raise HTTPException(status_code=403, detail="输入文件不属于当前用户")


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


def _is_explicit_cross_agent(agent_hint: str | None, selected_agent: str,
                             recovered_agent: str | None) -> bool:
    """显式选择其他 Agent 时开启新修订链。"""
    return bool(
        agent_hint and agent_hint != "auto" and recovered_agent
        and selected_agent != recovered_agent
    )


@router.post("/chat", response_model=BaseResponse[ChatResponse], summary="对话入口")
async def chat(req: ChatRequest, request: Request):
    """
    接收用户自然语言需求，自动识别意图并创建任务。
    走真正的数据库+任务队列，而非内存模拟。
    """
    _scan_user_prompt(req.message, request)
    user_id, user_role = _request_identity(request)

    from ...database.session import session_scope
    from ...database.repository import TaskRepository, FileRepository

    # 兼容前端发送的不同字段名
    agent_hint = req.agent_hint
    file_ids = list(req.file_ids or [])

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
        cross_agent_new_task = False
        if not file_ids and req.conversation_id:
            from ...storage.storage_service import get_storage_service
            recovered = _recover_conversation_context(
                req.conversation_id,
                task_repo,
                get_storage_service(),
                owner_id=user_id if user_role != "admin" else None,
            )
            if recovered:
                file_ids = []
                input_paths = recovered["input_paths"]
                # A follow-up inherits the prior agent/task route unless the
                # user explicitly selected another agent — or the message
                # clearly names a different one ("Word 会话里要 PPT").
                recovered_agent = recovered["task"].agent_name
                if _is_explicit_cross_agent(agent_hint, agent, recovered_agent):
                    cross_agent_new_task = True
                elif not agent_hint or agent_hint == "auto":
                    msg_agent, msg_task_type, msg_intent = route_intent(req.message)
                    if (msg_agent != "orchestrator" and recovered_agent
                            and msg_agent != recovered_agent):
                        agent, task_type, intent = msg_agent, msg_task_type, msg_intent
                        cross_agent_new_task = True
                    else:
                        agent = recovered_agent or agent
                        task_type = recovered["task"].task_type
                        intent = "follow_up"

        if file_ids:
            _require_owned_files(file_ids, file_repo, user_id, user_role)
            input_paths = resolve_input_files(file_ids, file_repo)
            # 路由裁决统一走 api.routing.resolve_route（文件类型权威、
            # 消息关键词仅在同 Agent 内细化子任务），与 worker 层同一实现。
            if (not agent_hint or agent_hint == "auto") and input_paths:
                agent, task_type, intent = resolve_route(req.message, input_paths[0])

        # 跨 Agent 的新任务不接入旧修订链（父任务/revision 只属于同一产物）
        continuation = bool(recovered) and not cross_agent_new_task
        parent_task_id = recovered["task"].id if continuation else None
        previous_instruction = recovered.get("previous_instruction") if continuation else None

        # 解析 PPT 模板文件（可选，用于按模板生成）
        template_path = None
        if req.template_file_id:
            _require_owned_files(
                [req.template_file_id], file_repo, user_id, user_role
            )
            template_paths = resolve_input_files([req.template_file_id], file_repo)
            template_path = template_paths[0] if template_paths else None

        # 从context中提取模型配置（仅保留非敏感字段，防止 API Key 进入任务数据）
        model_config = _sanitize_model_config(req.context.get("model_config") if req.context else None)
        history = _sanitize_history(req.context.get("history") if req.context else None)
        revision_mode = _classify_follow_up(req.message, continuation)
        revision_number = (recovered["task"].revision_number + 1) if continuation else 1
        if continuation:
            previous_history = _sanitize_history(recovered["options"].get("history"))
            # Keep the prior objective explicit even when the client only sends
            # assistant status text or has lost its local message history.
            prior_turn = {
                "role": "user",
                "content": recovered.get("previous_instruction", ""),
            }
            history = (previous_history + [prior_turn] + history)[-10:]

        from ...task_queue import DEFAULT_PRIORITY, PRIORITY_TO_INT
        db_task = task_repo.create_task(
            task_type=task_type,
            instruction=req.message,
            agent_name=agent,
            user_id=user_id,
            input_file_ids=json.dumps(file_ids) if file_ids else None,
            options_json=json.dumps({
                "intent": intent,
                "conversation_id": req.conversation_id,
                "input_paths": input_paths,
                "template_path": template_path,
                "model_config": model_config,
                "history": history,
                "is_follow_up": continuation,
                "parent_task_id": parent_task_id,
                "previous_instruction": previous_instruction,
                "revision_mode": revision_mode,
                "revision_number": revision_number,
                "_owner_id": user_id,
            }, ensure_ascii=False),
            priority=PRIORITY_TO_INT[DEFAULT_PRIORITY],
            parent_task_id=parent_task_id,
            revision_number=revision_number,
        )
        task_id = db_task.id

    # 2. 提交到真正的任务队列
    status = "queued"
    try:
        from ...task_queue import submit_task, init_worker, queue_name_for_task_type
        init_worker()

        queue_task_name = queue_name_for_task_type(task_type)
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
                    "template_path": template_path,
                    "history": history,
                    "is_follow_up": continuation,
                    "parent_task_id": parent_task_id,
                    "previous_instruction": previous_instruction,
                    "revision_mode": revision_mode,
                    "_owner_id": user_id,
                },
            },
            priority=DEFAULT_PRIORITY,
            task_id=task_id,
            task_type=task_type,
            instruction=req.message,
        )
        logger.info(f"聊天任务已提交到队列: {task_id} -> {queue_task_name} (agent={agent})")
    except Exception as e:
        from ...security.error_sanitizer import sanitize_error
        safe_error = sanitize_error(e, "任务队列提交失败")
        logger.error("任务队列提交失败: %s", safe_error, exc_info=True)
        with session_scope() as session:
            TaskRepository(session).fail_task(task_id, f"任务队列提交失败: {safe_error}")
        status = "failed"

    data = ChatResponse(
        task_id=task_id,
        conversation_id=req.conversation_id or str(uuid.uuid4()),
        agent=agent,
        intent=intent,
        status=status,
        message=f"已为您分配 {agent} 处理",
        estimated_time=30,
        is_follow_up=continuation,
        parent_task_id=parent_task_id,
        revision_mode=revision_mode,
        revision_number=revision_number,
    )
    return BaseResponse(data=data)
