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
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from ..schemas.request import TaskCreateRequest, FeedbackRequest
from ..schemas.response import (
    TaskInfo, TaskListResponse, TaskFileInfo, BaseResponse,
)
from ..core.task_manager import (
    normalize_task_status, task_manager, log_db_fallback,
)
from ..core.exceptions import APIError, TaskNotFoundError, TaskStateError
from ..core.file_resolution import resolve_input_files
from ..core.prompt_policy import enforce_user_prompt
from ..core.pagination import page_query, page_size_query
from ...security.error_sanitizer import sanitize_error
from ..core.config import settings

router = APIRouter(prefix="/api/task", tags=["任务"])


logger = logging.getLogger("office_agent.api.task")

# Backward-compatible private name for integrations that imported the old helper.
_resolve_input_files = resolve_input_files

# 可取消状态必须与 TaskRepository.cancel_task 的 SQL 条件一致
# （``status IN ("pending", "queued", "running")``）。上层判定与数据库实际
# 行为一旦漂移，就会重新出现"数据库没取消、接口却报成功"的假成功。
_CANCELLABLE_STATUSES = ("pending", "queued", "running")

# Clients choose intent and benign rendering options. File locations,
# ownership, model credentials, and network/MCP endpoints are server-owned.
_SERVER_CONTROLLED_TASK_OPTIONS = frozenset({
    "_owner_id",
    "input_file_ids",
    "input_path",
    "input_paths",
    "output_path",
    "template_path",
    "image_model_config",
})


def _sanitize_client_task_options(options) -> dict:
    """Reject attempts to smuggle server capabilities through generic options."""
    if not options:
        return {}
    if not isinstance(options, dict):
        raise HTTPException(status_code=422, detail="任务选项必须是对象")
    forbidden = sorted(_SERVER_CONTROLLED_TASK_OPTIONS.intersection(options))
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SERVER_CONTROLLED_TASK_OPTION",
                "message": "任务选项包含仅限服务端设置的字段",
                "fields": forbidden,
            },
        )
    return dict(options)


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
            # original_name 是用户可见文件名的唯一权威字段；
            # 响应 key 仍叫 filename 属外部 API 兼容契约。
            "filename": db_file.original_name,
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


def _task_info_from_db(db_task, file_repo) -> TaskInfo:
    """Build the persisted-task response shape from an ORM task."""
    return TaskInfo(
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
        storage="persisted",
        degraded=False,
    )


def _task_info_from_memory(task) -> TaskInfo:
    """Build the degraded memory-task response without touching the DB."""
    return TaskInfo(
        task_id=task.task_id,
        task_type=task.task_type,
        agent=task.agent or "",
        status=task.status,
        progress=task.progress or 0,
        current_step=task.current_step,
        instruction=task.instruction,
        error=task.error,
        input_files=[TaskFileInfo(file_id=file_id, filename=file_id,
                                  download_url=f"/api/file/download/{file_id}")
                     for file_id in (task.input_files or [])],
        output_files=[TaskFileInfo(file_id=file_id, filename=file_id,
                                   download_url=f"/api/file/download/{file_id}")
                      for file_id in (task.output_files or [])],
        result=_sanitize_result(task.result) if task.result else None,
        created_at=task.created_at or "",
        started_at=task.started_at,
        completed_at=task.completed_at,
        duration_ms=task.duration_ms,
        quality_score=task.quality_score,
        storage=task.storage,
        degraded=task.degraded,
    )


def _get_db_session():
    try:
        from ...database.session import SessionLocal
        return SessionLocal()
    except SQLAlchemyError:
        # 与 Agent 接口一致：降级到非数据库路径前必须留下结构化日志
        logger.exception("创建数据库会话失败，任务接口将降级到非数据库路径")
        return None


@router.post("/create", response_model=BaseResponse[TaskInfo],
             summary="创建任务")
async def create_task(req: TaskCreateRequest, request: Request):
    """
    创建处理任务（异步队列执行）

    - **task_type**: 任务类型 word_format/ppt_generate/excel_analyze等
    - **instruction**: 任务指令
    - **file_ids**: 输入文件ID列表
    - **priority**: high/normal/low
    """
    return await _create_task_impl(req, request)


async def _create_task_impl(req: TaskCreateRequest, request: Request | None = None):
    """``create_task`` 的内部实现，允许直接 Python 调用时省略 request。"""
    from ...database.session import session_scope
    from ...database.repository import TaskRepository, FileRepository
    from ...task_queue import (
        TASK_TYPE_TO_QUEUE, DEFAULT_PRIORITY, VALID_PRIORITIES, PRIORITY_TO_INT,
    )

    enforce_user_prompt(req.instruction, request)

    if req.task_type not in TASK_TYPE_TO_QUEUE:
        raise HTTPException(status_code=422, detail=f"不支持的任务类型: {req.task_type}")

    priority = (req.priority or DEFAULT_PRIORITY) if hasattr(req, "priority") else DEFAULT_PRIORITY
    if priority not in VALID_PRIORITIES:
        priority = DEFAULT_PRIORITY
    agent_name = _agent_name_for_task_type(req.task_type)

    user_id, user_role = _request_identity(request)
    task_options = _sanitize_client_task_options(req.options)
    if user_id:
        # Server-controlled key: a client-supplied value must never choose the
        # owner of generated output files.
        task_options["_owner_id"] = user_id

    input_files = list(req.file_ids or [])
    input_paths = []

    # 1. 先解析输入文件（DB 读）。文件路径解析失败时不能创建一个注定
    # 无法执行的 memory task，因此该读取失败采用 fail-closed。
    if input_files:
        try:
            with session_scope() as session:
                file_repo = FileRepository(session)
                if user_id and user_role != "admin":
                    for file_id in input_files:
                        db_file = file_repo.get_by_id(file_id)
                        if not db_file or db_file.owner_id != user_id:
                            raise HTTPException(status_code=403, detail="输入文件不属于当前用户")
                input_paths = resolve_input_files(input_files, file_repo)
        except SQLAlchemyError as exc:
            log_db_fallback("create_task_resolve_inputs", None, exc)
            raise APIError(
                "数据库暂不可用，无法解析输入文件",
                error_code="DATABASE_UNAVAILABLE",
                status_code=503,
            ) from exc

    # 2. 创建持久化记录。只有 DB 写失败时才允许有限的进程内 memory
    # fallback；memory 任务不能伪装成 persisted。
    task_id = None
    storage = "persisted"
    degraded = False
    try:
        with session_scope() as session:
            task_repo = TaskRepository(session)
            db_task = task_repo.create_task(
                task_type=req.task_type,
                instruction=req.instruction,
                agent_name=agent_name,
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
    except SQLAlchemyError as exc:
        log_db_fallback("create_task", None, exc)
        memory_task = task_manager.create_memory_task(
            task_type=req.task_type,
            instruction=req.instruction,
            agent=agent_name or "",
            file_ids=input_files,
            options=task_options,
            user_id=user_id,
            status="pending",
        )
        task_id = memory_task.task_id
        storage = "memory"
        degraded = True

    # 3. 提交到任务队列
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
        if degraded:
            task_manager.update_memory_task(task_id, status="queued", progress=0)
    except Exception as e:
        # 队列不可用时把数据库任务标记为失败，避免双账本（内存任务与数据库记录不一致）
        safe_error = sanitize_error(e, "任务队列不可用")
        logger.error("任务队列不可用: %s", safe_error, exc_info=True)
        now_iso = datetime.now(timezone.utc).isoformat()
        if degraded:
            task_manager.update_memory_task(
                task_id,
                status="failed",
                progress=100,
                error=f"任务队列不可用: {safe_error}",
                completed_at=now_iso,
            )
        else:
            try:
                with session_scope() as session:
                    TaskRepository(session).fail_task(
                        task_id, f"任务队列不可用: {safe_error}",
                    )
            except SQLAlchemyError as db_exc:
                log_db_fallback("fail_task_after_queue_error", task_id, db_exc)
                task_manager.create_memory_task(
                    task_id=task_id,
                    task_type=req.task_type,
                    instruction=req.instruction,
                    agent=agent_name or "",
                    file_ids=input_files,
                    options=task_options,
                    user_id=user_id,
                    status="failed",
                )
                task_manager.update_memory_task(
                    task_id,
                    progress=100,
                    error=f"任务队列不可用: {safe_error}",
                    completed_at=now_iso,
                )
        status = "failed"

    return BaseResponse(data=TaskInfo(
        task_id=task_id,
        task_type=req.task_type,
        agent=agent_name or "",
        status=status,
        progress=0,
        instruction=req.instruction,
        storage=storage,
        degraded=degraded,
    ))


@router.get("/{task_id}", response_model=BaseResponse[TaskInfo],
            summary="查询任务状态")
async def get_task(task_id: str):
    """
    查询任务状态和进度

    状态: pending/queued/running/success/failed/cancelled
    """
    session = _get_db_session()
    db_available = False
    if session:
        try:
            from ...database.repository import TaskRepository, FileRepository
            repo = TaskRepository(session)
            file_repo = FileRepository(session)
            db_task = repo.get_by_id(task_id)
            if db_task:
                memory_task = task_manager.get_task(task_id)
                if memory_task is not None and memory_task.degraded:
                    return BaseResponse(data=_task_info_from_memory(memory_task))
                return BaseResponse(data=_task_info_from_db(db_task, file_repo))
            db_available = True
        except SQLAlchemyError as exc:
            log_db_fallback("get_task", task_id, exc)
        finally:
            session.close()

    # 回退到内存
    task = task_manager.get_task(task_id)
    if task:
        return BaseResponse(data=_task_info_from_memory(task))

    if settings.auth_enabled and not db_available:
        raise APIError(
            "数据库暂不可用，无法查询任务",
            error_code="DATABASE_UNAVAILABLE",
            status_code=503,
        )

    raise TaskNotFoundError(f"任务不存在: {task_id}")


@router.get("/", response_model=BaseResponse[TaskListResponse],
            summary="任务列表")
async def list_tasks(request: Request, status: str | None = None,
                     agent: str | None = None,
                     page: int = page_query(),
                     page_size: int = page_size_query()):
    """列出任务，支持按状态/Agent筛选"""
    return await _list_tasks_impl(status, agent, page, page_size, request)


async def _list_tasks_impl(status: str | None = None, agent: str | None = None,
                           page: int = page_query(),
                           page_size: int = page_size_query(),
                           request: Request | None = None):
    """``list_tasks`` 的内部实现，允许直接 Python 调用时省略 request。"""
    user_id, user_role = _request_identity(request)
    normalized_status = None
    if status:
        try:
            normalized_status = normalize_task_status(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    memory_owner = user_id if user_id and user_role != "admin" else None
    memory_tasks = task_manager.snapshot_tasks(
        status=normalized_status, agent=agent, user_id=memory_owner,
    )

    session = _get_db_session()
    if session:
        try:
            from ...database.repository import TaskRepository, FileRepository
            repo = TaskRepository(session)
            file_repo = FileRepository(session)
            filters = {}
            if memory_owner:
                filters["user_id"] = user_id
            if normalized_status:
                filters["status"] = normalized_status
            if agent:
                filters["agent_name"] = agent

            if not memory_tasks:
                db_tasks = repo.find(
                    offset=(page - 1) * page_size,
                    limit=page_size,
                    order_by="created_at",
                    descending=True,
                    **filters,
                )
                total = repo.count(**filters)
                return BaseResponse(data=TaskListResponse(
                    tasks=[_task_info_from_db(t, file_repo) for t in db_tasks],
                    total=total,
                    page=page,
                    page_size=page_size,
                ))

            # 有 degraded/memory-only 快照时，内存是该任务在当前进程内的
            # 权威视图。合并 DB 与内存视图可避免 get/list 出现不同结果。
            # 深分页必须把 offset 下推数据库。历史实现固定 offset=0/limit=MAX_LIMIT
            # 先取前 1000 行、再在内存里切片，任务数超过 MAX_LIMIT 时深页
            # 永远读不到数据（内存切片无法补救没读出来的行）。
            #
            # 内存任务按 created_at 与 DB 行交错，会把窗口内的 DB 行往后挤，
            # 因此窗口要向前多取若干行。这里用 memory_tasks 总数作为
            # memory-only 数量的上界——上界只会让窗口更早、取更多行，
            # 不会漏行；且这样可以在调用 find 之前就算出窗口，
            # 保住"find 抛 SQLAlchemyError 即整体降级到内存列表"的既有契约。
            from ...database.repository.base import MAX_LIMIT
            start = (page - 1) * page_size
            lookback = len(memory_tasks)
            db_offset = max(0, start - lookback)
            db_limit = min(lookback + page_size, MAX_LIMIT)
            db_tasks = repo.find(
                offset=db_offset,
                limit=db_limit,
                order_by="created_at",
                descending=True,
                **filters,
            )
            db_total = repo.count(**filters)

            # memory-only 判定必须基于整库存在性，而不是当前分页窗口内的行：
            # offset 下推后，窗口之外的行同样存在于 DB，用窗口判定会把
            # 已落库任务误当成 memory-only 而重复计入 total。
            db_present = repo.filter_existing_ids([t.task_id for t in memory_tasks])
            memory_by_id = {t.task_id: t for t in memory_tasks}
            memory_only = [t for t in memory_tasks if t.task_id not in db_present]
            merged = [
                _task_info_from_memory(memory_by_id[t.id])
                if t.id in memory_by_id else _task_info_from_db(t, file_repo)
                for t in db_tasks
            ]
            merged.extend(_task_info_from_memory(t) for t in memory_only)
            merged.sort(key=lambda item: item.created_at, reverse=True)
            total = db_total + len(memory_only)

            # merged 是"DB 窗口 + 全部 memory-only"的排序结果。窗口之前的
            # db_offset 行在完整列表里同样位于窗口之前，所以完整列表下标
            # start 对应窗口内下标 start - db_offset。
            window_start = start - db_offset
            return BaseResponse(data=TaskListResponse(
                tasks=merged[window_start:window_start + page_size],
                total=total,
                page=page,
                page_size=page_size,
            ))
        except SQLAlchemyError as exc:
            log_db_fallback("list_tasks", None, exc)
        finally:
            session.close()

    # 数据库不可用时只回退到已降级的内存快照；DB-backed 任务不可见。
    if memory_tasks:
        tasks, total = task_manager.list_tasks(
            status=normalized_status, agent=agent, page=page, page_size=page_size,
        )
        return BaseResponse(data=TaskListResponse(
            tasks=[_task_info_from_memory(t) for t in tasks],
            total=total, page=page, page_size=page_size,
        ))

    if settings.auth_enabled:
        raise APIError(
            "数据库暂不可用，无法列出任务",
            error_code="DATABASE_UNAVAILABLE",
            status_code=503,
        )

    return BaseResponse(data=TaskListResponse(
        tasks=[], total=0, page=page, page_size=page_size,
    ))


@router.post("/{task_id}/cancel", response_model=BaseResponse,
             summary="取消任务")
async def cancel_task(task_id: str, request: Request):
    """取消任务"""
    return await _cancel_task_impl(task_id, request)


async def _cancel_task_impl(task_id: str, request: Request | None = None):
    """``cancel_task`` 的内部实现，允许直接 Python 调用时省略 request。"""
    # 尝试从队列取消
    try:
        from ...task_queue import cancel_task as queue_cancel
        queue_cancel(task_id)
    except Exception:
        pass

    memory_task = task_manager.get_task(task_id)
    session = _get_db_session()

    # 数据库不可用时任何内存快照都是唯一可用视图；数据库可用但存在
    # degraded 快照时，内存仍是该任务在当前进程内的权威视图。
    if session is None:
        if memory_task is not None:
            if memory_task.status in _CANCELLABLE_STATUSES:
                task_manager.cancel_memory_task(task_id)
            else:
                # 已终态：不得再回复"任务已取消"
                raise TaskStateError(
                    f"任务已处于终态（{memory_task.status}），无法取消: {task_id}")
            return BaseResponse(message="任务已取消")
    elif memory_task is not None and memory_task.degraded:
        if memory_task.status in _CANCELLABLE_STATUSES:
            task_manager.cancel_memory_task(task_id)
        else:
            raise TaskStateError(
                f"任务已处于终态（{memory_task.status}），无法取消: {task_id}")
        return BaseResponse(message="任务已取消")

    # 更新数据库
    db_available = False
    if session:
        try:
            from ...database.repository import TaskRepository
            repo = TaskRepository(session)
            db_task = repo.get_by_id(task_id)
            if db_task:
                # repo.cancel_task 仅在任务仍处于可取消状态时返回 True。
                # 返回 False 表示任务已终态（success/failed/cancelled）——
                # 历史实现对此无 else 分支，照样回复"任务已取消"，构成假成功。
                if not repo.cancel_task(task_id):
                    raise TaskStateError(
                        f"任务已处于终态（{db_task.status}），无法取消: {task_id}")
                session.commit()
                user_id = getattr(request.state, "user_id", None) if request else None
                if user_id == "anonymous":
                    user_id = None
                try:
                    from ...security.audit import get_audit_logger
                    get_audit_logger().log_task_transition(
                        task_id, "cancelled", user_id=user_id)
                except Exception:
                    # 审计失败不改变取消结果（fail-open）
                    pass
                db_available = True
                return BaseResponse(message="任务已取消")
            db_available = True
        except SQLAlchemyError as exc:
            log_db_fallback("cancel_task", task_id, exc)
        finally:
            session.close()

    if settings.auth_enabled and not db_available:
        raise APIError(
            "数据库暂不可用，无法取消任务",
            error_code="DATABASE_UNAVAILABLE",
            status_code=503,
        )

    raise TaskNotFoundError(f"任务不存在: {task_id}")


@router.post("/{task_id}/feedback", response_model=BaseResponse,
             summary="任务反馈")
async def task_feedback(task_id: str, req: FeedbackRequest):
    """对任务结果提交反馈"""
    memory_task = task_manager.get_task(task_id)
    session = _get_db_session()
    if session is None:
        if memory_task is not None:
            memory_task.feedback_rating = req.rating
            memory_task.feedback_comment = req.comment
            return BaseResponse(message="反馈已提交", data={
                "task_id": task_id, "rating": req.rating, "comment": req.comment,
                "storage": "memory",
            })
        raise APIError(
            "数据库暂不可用，反馈未保存",
            error_code="DATABASE_UNAVAILABLE",
            status_code=503,
        )

    db_task = None
    try:
        from ...database.repository import TaskRepository
        repo = TaskRepository(session)
        db_task = repo.get_by_id(task_id)
        if db_task is None:
            if memory_task is not None:
                memory_task.feedback_rating = req.rating
                memory_task.feedback_comment = req.comment
                return BaseResponse(message="反馈已提交", data={
                    "task_id": task_id, "rating": req.rating,
                    "comment": req.comment, "storage": "memory",
                })
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        if memory_task is not None and memory_task.degraded:
            memory_task.feedback_rating = req.rating
            memory_task.feedback_comment = req.comment
            return BaseResponse(message="反馈已提交", data={
                "task_id": task_id, "rating": req.rating, "comment": req.comment,
                "storage": "memory",
            })
        repo.add_feedback(task_id, req.rating, req.comment)
        session.commit()
        return BaseResponse(message="反馈已提交", data={
            "task_id": task_id, "rating": req.rating, "comment": req.comment,
        })
    except SQLAlchemyError as exc:
        if session is not None:
            session.rollback()
        log_db_fallback("task_feedback", task_id, exc)
        if memory_task is None and db_task is not None:
            memory_task = task_manager.create_memory_task(
                task_id=db_task.id,
                task_type=db_task.task_type,
                instruction=db_task.instruction,
                agent=db_task.agent_name or "",
                user_id=db_task.user_id,
            )
        if memory_task is not None:
            memory_task.feedback_rating = req.rating
            memory_task.feedback_comment = req.comment
            return BaseResponse(message="反馈已提交", data={
                "task_id": task_id, "rating": req.rating, "comment": req.comment,
                "storage": "memory",
            })
        raise APIError(
            "数据库暂不可用，反馈未保存",
            error_code="DATABASE_UNAVAILABLE",
            status_code=503,
        ) from exc
    finally:
        session.close()
