"""
任务路由 - 创建/查询/管理任务
集成任务队列 + 数据库持久化

流程：
    POST /api/task/create
        → 创建数据库记录 (pending)
        → 提交到任务队列 (queued)
        → Worker 异步执行 (running → success/failed)
        → 立即返回 task_id
"""
import json
import logging
from fastapi import APIRouter, HTTPException, Query, Request

from ..schemas.request import TaskCreateRequest, FeedbackRequest
from ..schemas.response import (
    TaskInfo, TaskListResponse, BaseResponse,
)
from ..core.task_manager import normalize_task_status, task_manager
from ..core.exceptions import APIError, TaskNotFoundError
from ..core.file_resolution import resolve_input_files
from ...security.error_sanitizer import sanitize_error
from ..core.config import settings

router = APIRouter(prefix="/api/task", tags=["任务"])


logger = logging.getLogger("office_agent.api.task")

# Backward-compatible private name for integrations that imported the old helper.
_resolve_input_files = resolve_input_files


def _agent_name_for_task_type(task_type: str | None) -> str | None:
    """从任务类型推导所属 Agent；无法推导时统一返回 None。

    单一"无值"口径：内部/落库一律用 None，响应层在序列化时显式 `or ""`。
    """
    if not task_type or "_" not in task_type:
        return None
    return task_type.split("_")[0] + "_agent"


def _request_identity(request: Request | None) -> tuple[str | None, str]:
    if not settings.auth_enabled:
        return None, ""
    user_id = getattr(request.state, "user_id", None) if request else None
    role = getattr(request.state, "user_role", "") if request else ""
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="缺少已认证用户")
    return user_id, role


def _safe_json_object(raw: str | None, default):
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning("任务记录包含损坏的 JSON，已隔离")
        return default
    return value


def _file_id_list_to_infos(file_id_list, file_repo) -> list:
    """Convert file IDs to stable output/input file info objects (response layer only)."""
    infos = []
    for file_id in file_id_list or []:
        db_file = file_repo.get_by_id(file_id)
        if not db_file or db_file.status == "deleted":
            continue
        infos.append({
            "file_id": db_file.id,
            "filename": db_file.original_name or db_file.filename,
            "download_url": f"/api/file/download/{db_file.id}",
        })
    return infos


def _sanitize_result(result: dict) -> dict:
    """Strip local filesystem paths from the API response (DB result_json is untouched)."""
    if not isinstance(result, dict):
        return result
    safe = dict(result)
    safe.pop("output_path", None)
    return safe


def _get_db_session():
    try:
        from ...database.session import SessionLocal
        return SessionLocal()
    except Exception:
        return None


@router.post("/create", response_model=BaseResponse[TaskInfo],
             summary="创建任务")
async def create_task(req: TaskCreateRequest, request: Request = None):
    """
    创建处理任务（异步队列执行）

    - **task_type**: 任务类型 word_format/ppt_generate/excel_analyze等
    - **instruction**: 任务指令
    - **file_ids**: 输入文件ID列表
    - **priority**: high/normal/low
    """
    from ...database.session import session_scope
    from ...database.repository import TaskRepository, FileRepository
    from ...task_queue import (
        TASK_TYPE_TO_QUEUE, DEFAULT_PRIORITY, VALID_PRIORITIES, PRIORITY_TO_INT,
    )

    if req.task_type not in TASK_TYPE_TO_QUEUE:
        raise HTTPException(status_code=422, detail=f"不支持的任务类型: {req.task_type}")

    priority = (req.priority or DEFAULT_PRIORITY) if hasattr(req, "priority") else DEFAULT_PRIORITY
    if priority not in VALID_PRIORITIES:
        priority = DEFAULT_PRIORITY

    # 1. 创建数据库记录
    task_id = None
    input_files = []
    user_id, user_role = _request_identity(request)
    task_options = dict(req.options or {})
    if user_id:
        # Server-controlled key: a client-supplied value must never choose the
        # owner of generated output files.
        task_options["_owner_id"] = user_id
    with session_scope() as session:
        task_repo = TaskRepository(session)
        file_repo = FileRepository(session)

        # 获取输入文件路径
        input_paths = []
        if req.file_ids:
            if user_id and user_role != "admin":
                for file_id in req.file_ids:
                    db_file = file_repo.get_by_id(file_id)
                    if not db_file or db_file.owner_id != user_id:
                        raise HTTPException(status_code=403, detail="输入文件不属于当前用户")
            input_paths = resolve_input_files(req.file_ids, file_repo)
            input_files = list(req.file_ids)

        db_task = task_repo.create_task(
            task_type=req.task_type,
            instruction=req.instruction,
            agent_name=_agent_name_for_task_type(req.task_type),
            user_id=user_id,
            input_file_ids=json.dumps(input_files) if input_files else None,
            options_json=json.dumps({
                "output_format": getattr(req, "output_format", None),
                "options": task_options,
                "input_paths": input_paths,
            }, ensure_ascii=False),
            priority=PRIORITY_TO_INT.get(priority, PRIORITY_TO_INT[DEFAULT_PRIORITY]),
        )
        task_id = db_task.id

    # 2. 提交到任务队列
    try:
        from ...task_queue import submit_task, init_worker, queue_name_for_task_type
        init_worker()

        queue_task_name = queue_name_for_task_type(req.task_type)
        options = task_options
        input_path = input_paths[0] if input_paths else None

        submit_task(
            task_name=queue_task_name,
            kwargs={
                "input_path": input_path,
                "instruction": req.instruction,
                "input_paths": input_paths,
                "options": {
                    **options,
                    "input_file_ids": input_files,
                    "input_paths": input_paths,
                },
                "output_format": getattr(req, "output_format", None),
            },
            priority=priority,
            task_id=task_id,
            task_type=req.task_type,
            instruction=req.instruction,
        )
        status = "queued"
    except Exception as e:
        # 队列不可用时把数据库任务标记为失败，避免双账本（内存任务与数据库记录不一致）
        safe_error = sanitize_error(e, "任务队列不可用")
        logger.error("任务队列不可用: %s", safe_error, exc_info=True)
        with session_scope() as session:
            TaskRepository(session).fail_task(task_id, f"任务队列不可用: {safe_error}")
        status = "failed"

    return BaseResponse(data=TaskInfo(
        task_id=task_id,
        task_type=req.task_type,
        agent=_agent_name_for_task_type(req.task_type) or "",
        status=status,
        progress=0,
        instruction=req.instruction,
    ))


@router.get("/{task_id}", response_model=BaseResponse[TaskInfo],
            summary="查询任务状态")
async def get_task(task_id: str):
    """
    查询任务状态和进度

    状态: pending/queued/running/success/failed/cancelled
    """
    # 优先从数据库取（队列任务状态在数据库）
    session = _get_db_session()
    if session:
        try:
            from ...database.repository import TaskRepository, FileRepository
            repo = TaskRepository(session)
            file_repo = FileRepository(session)
            db_task = repo.get_by_id(task_id)
            if db_task:
                return BaseResponse(data=TaskInfo(
                    task_id=db_task.id,
                    task_type=db_task.task_type,
                    agent=db_task.agent_name or "",
                    status=db_task.status,
                    progress=db_task.progress or 0,
                    current_step=db_task.current_step,
                    instruction=db_task.instruction,
                    error=db_task.error_message,
                    input_files=_file_id_list_to_infos(_safe_json_object(db_task.input_file_ids, []), file_repo),
                    output_files=_file_id_list_to_infos(_safe_json_object(db_task.output_file_ids, []), file_repo),
                    result=_sanitize_result(_safe_json_object(db_task.result_json, {})) if db_task.result_json else None,
                    created_at=str(db_task.created_at) if db_task.created_at else "",
                    started_at=str(db_task.started_at) if db_task.started_at else None,
                    completed_at=str(db_task.finished_at) if db_task.finished_at else None,
                    duration_ms=db_task.duration_ms,
                    quality_score=db_task.quality_score,
                    parent_task_id=db_task.parent_task_id,
                    revision_number=db_task.revision_number or 1,
                ))
        finally:
            session.close()

        if settings.auth_enabled:
            raise TaskNotFoundError(f"任务不存在: {task_id}")

    if settings.auth_enabled:
        raise APIError(
            "数据库暂不可用，无法查询任务",
            error_code="DATABASE_UNAVAILABLE",
            status_code=503,
        )

    # 回退到内存
    task = task_manager.get_task(task_id)
    if task:
        return BaseResponse(data=TaskInfo(**task.to_dict()))

    raise TaskNotFoundError(f"任务不存在: {task_id}")


@router.get("/", response_model=BaseResponse[TaskListResponse],
            summary="任务列表")
async def list_tasks(status: str = None, agent: str = None,
                     page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=20, ge=1, le=200),
                     request: Request = None):
    """列出任务，支持按状态/Agent筛选"""
    session = _get_db_session()
    if session:
        try:
            from ...database.repository import TaskRepository, FileRepository
            repo = TaskRepository(session)
            file_repo = FileRepository(session)
            filters = {}
            user_id, user_role = _request_identity(request)
            if user_id and user_role != "admin":
                filters["user_id"] = user_id
            if status:
                try:
                    filters["status"] = normalize_task_status(status)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            if agent:
                filters["agent_name"] = agent
            db_tasks = repo.find(offset=(page - 1) * page_size, limit=page_size,
                                 order_by="created_at", descending=True, **filters)
            total = repo.count(**filters)
            task_infos = [TaskInfo(
                task_id=t.id, task_type=t.task_type, agent=t.agent_name or "",
                status=t.status, progress=t.progress or 0,
                current_step=t.current_step, instruction=t.instruction,
                input_files=_file_id_list_to_infos(_safe_json_object(t.input_file_ids, []), file_repo),
                output_files=_file_id_list_to_infos(_safe_json_object(t.output_file_ids, []), file_repo),
                result=_sanitize_result(_safe_json_object(t.result_json, {})) if t.result_json else None,
                error=t.error_message,
                created_at=str(t.created_at) if t.created_at else "",
                duration_ms=t.duration_ms, quality_score=t.quality_score,
                parent_task_id=t.parent_task_id, revision_number=t.revision_number or 1,
            ) for t in db_tasks]
            return BaseResponse(data=TaskListResponse(
                tasks=task_infos, total=total, page=page, page_size=page_size
            ))
        finally:
            session.close()

    if settings.auth_enabled:
        raise APIError(
            "数据库暂不可用，无法列出任务",
            error_code="DATABASE_UNAVAILABLE",
            status_code=503,
        )

    # 回退到内存
    tasks, total = task_manager.list_tasks(status=status, agent=agent, page=page, page_size=page_size)
    return BaseResponse(data=TaskListResponse(
        tasks=[TaskInfo(**t.to_dict()) for t in tasks],
        total=total, page=page, page_size=page_size,
    ))


@router.post("/{task_id}/cancel", response_model=BaseResponse,
             summary="取消任务")
async def cancel_task(task_id: str):
    """取消任务"""
    # 尝试从队列取消
    try:
        from ...task_queue import cancel_task as queue_cancel
        queue_cancel(task_id)
    except Exception:
        pass

    # 更新数据库
    session = _get_db_session()
    if session:
        try:
            from ...database.repository import TaskRepository
            repo = TaskRepository(session)
            if repo.get_by_id(task_id):
                repo.cancel_task(task_id)
                session.commit()
                return BaseResponse(message="任务已取消")
        finally:
            session.close()

        if settings.auth_enabled:
            raise TaskNotFoundError(f"任务不存在: {task_id}")

    if settings.auth_enabled:
        raise APIError(
            "数据库暂不可用，无法取消任务",
            error_code="DATABASE_UNAVAILABLE",
            status_code=503,
        )

    # 回退到内存
    task = task_manager.get_task(task_id)
    if task:
        task.status = "cancelled"
        return BaseResponse(message="任务已取消")

    raise TaskNotFoundError(f"任务不存在: {task_id}")


@router.post("/{task_id}/feedback", response_model=BaseResponse,
             summary="任务反馈")
async def task_feedback(task_id: str, req: FeedbackRequest):
    """对任务结果提交反馈"""
    session = _get_db_session()
    if session is None:
        if settings.auth_enabled:
            raise APIError(
                "数据库暂不可用，反馈未保存",
                error_code="DATABASE_UNAVAILABLE",
                status_code=503,
            )
        task = task_manager.get_task(task_id)
        if task is not None:
            task.feedback_rating = req.rating
            task.feedback_comment = req.comment
            return BaseResponse(message="反馈已提交", data={
                "task_id": task_id, "rating": req.rating, "comment": req.comment,
                "storage": "memory",
            })
        raise APIError(
            "数据库暂不可用，反馈未保存",
            error_code="DATABASE_UNAVAILABLE",
            status_code=503,
        )

    try:
        from ...database.repository import TaskRepository
        repo = TaskRepository(session)
        if not repo.get_by_id(task_id):
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        repo.add_feedback(task_id, req.rating, req.comment)
        session.commit()
        return BaseResponse(message="反馈已提交", data={
            "task_id": task_id, "rating": req.rating, "comment": req.comment,
        })
    finally:
        session.close()
