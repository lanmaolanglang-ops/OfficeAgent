"""
对话路由 - 自然语言入口
"""
import asyncio
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
from ..core.prompt_policy import enforce_user_prompt

router = APIRouter(prefix="/api", tags=["对话"])

logger = logging.getLogger("office_agent.api.chat")

def _scan_user_prompt(message: str, request: Request):
    """Backward-compatible wrapper around the shared API prompt policy."""
    return enforce_user_prompt(message, request)


def _recover_conversation_context(conversation_id: str, task_repo, storage,
                                  owner_id: str | None = None):
    """Recover the latest artifact and routing context when the client omits it.

    优先走 conversation_id 结构化索引（P2-18）；仅当旧数据列为空时
    才做有限 legacy 回退。
    """
    if not conversation_id:
        return None

    def _context_from_task(task, options):
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

    # 新路径：索引查询
    try:
        tasks = task_repo.get_by_conversation(
            conversation_id, user_id=owner_id, limit=20,
        )
        for task in tasks:
            try:
                options = json.loads(task.options_json or "{}")
            except (TypeError, ValueError):
                options = {}
            ctx = _context_from_task(task, options)
            if ctx:
                return ctx
    except Exception:
        pass

    # Legacy：仅当结构化列尚未回填时，有限扫描（新任务不再依赖此路径）
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
            ctx = _context_from_task(task, options)
            if ctx:
                return ctx
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


def _prepare_chat_task(*, req, user_id, user_role, agent_hint, agent,
                       task_type, intent, file_ids, had_conversation=False):
    """同步准备阶段：会话恢复 → 文件解析 → 创建任务记录。

    会话恢复会分页扫描任务表并对候选产物做文件系统 stat，长会话历史下
    属于无界磁盘 + DB 工作；直接在事件循环执行会阻塞同进程所有请求
    （桌面端 /health 探针被饿死会误触发后端自动重启）。调用方必须通过
    ``asyncio.to_thread`` 把本函数卸载到工作线程。Session 在本线程内
    创建并关闭，不跨线程复用；返回值只含可安全跨线程的纯数据。
    """
    from ...database.session import session_scope
    from ...database.repository import TaskRepository, FileRepository

    input_paths = []
    with session_scope() as session:
        task_repo = TaskRepository(session)
        file_repo = FileRepository(session)

        recovered = None
        cross_agent_new_task = False
        # 仅当 conversation_id 由客户端回传（可能存在历史）时才做恢复；
        # 本轮新铸 cid 的首轮对话必然无历史，跳过无界 legacy 扫描。
        if not file_ids and req.conversation_id and had_conversation:
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
            conversation_id=req.conversation_id or None,
        )
        task_id = db_task.id

    return {
        "task_id": task_id,
        "file_ids": file_ids,
        "input_paths": input_paths,
        "agent": agent,
        "task_type": task_type,
        "intent": intent,
        "continuation": continuation,
        "parent_task_id": parent_task_id,
        "previous_instruction": previous_instruction,
        "revision_mode": revision_mode,
        "revision_number": revision_number,
        "history": history,
        "model_config": model_config,
        "template_path": template_path,
    }


@router.post("/chat", response_model=BaseResponse[ChatResponse], summary="对话入口")
async def chat(req: ChatRequest, request: Request):
    """
    接收用户自然语言需求，自动识别意图并创建任务。
    走真正的数据库+任务队列，而非内存模拟。
    """
    _scan_user_prompt(req.message, request)
    user_id, user_role = _request_identity(request)

    from ...database.session import session_scope
    from ...database.repository import TaskRepository

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

    # 首轮客户端未带 conversation_id 时，必须在创建任务**之前**生成并回写到
    # req：否则只在响应里返回的 cid 不会落到任务行，第二轮按该 cid 查不到首任务，
    # follow-up 修订链随之断裂（RC：前端正是从首轮响应取 cid 再回传）。
    had_conversation = bool(req.conversation_id)
    if not req.conversation_id:
        req.conversation_id = str(uuid.uuid4())

    # 1. 创建数据库记录并获取文件路径。准备阶段的会话恢复会分页扫描任务表
    # 并对候选产物做文件系统 stat，属于无界磁盘 + DB 工作，卸载到工作线程，
    # 不阻塞事件循环；Session 在工作线程内创建并关闭，不跨线程复用。
    prepared = await asyncio.to_thread(
        _prepare_chat_task,
        req=req, user_id=user_id, user_role=user_role,
        agent_hint=agent_hint, agent=agent, task_type=task_type,
        intent=intent, file_ids=file_ids,
        had_conversation=had_conversation,
    )
    task_id = prepared["task_id"]
    file_ids = prepared["file_ids"]
    input_paths = prepared["input_paths"]
    agent = prepared["agent"]
    task_type = prepared["task_type"]
    intent = prepared["intent"]
    continuation = prepared["continuation"]
    parent_task_id = prepared["parent_task_id"]
    previous_instruction = prepared["previous_instruction"]
    revision_mode = prepared["revision_mode"]
    revision_number = prepared["revision_number"]
    history = prepared["history"]
    model_config = prepared["model_config"]
    template_path = prepared["template_path"]

    # 2. 提交到真正的任务队列
    status = "queued"
    try:
        from ...task_queue import (
            submit_task, init_worker, queue_name_for_task_type,
            DEFAULT_PRIORITY,
        )
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
        # 队列不可用时不得返回 200 冒充成功（P2-17）
        from ..core.exceptions import APIError
        raise APIError(
            "任务队列暂时不可用，请稍后重试",
            error_code="QUEUE_UNAVAILABLE",
            status_code=503,
        ) from e

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
