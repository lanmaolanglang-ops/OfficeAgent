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
import os
from fastapi import APIRouter, HTTPException

from ..schemas.request import TaskCreateRequest, FeedbackRequest
from ..schemas.response import (
    TaskInfo, TaskListResponse, BaseResponse,
)
from ..core.task_manager import task_manager
from ..core.exceptions import TaskNotFoundError

router = APIRouter(prefix="/api/task", tags=["任务"])


logger = logging.getLogger("office_agent.api.task")


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
async def create_task(req: TaskCreateRequest):
    """
    创建处理任务（异步队列执行）

    - **task_type**: 任务类型 word_format/ppt_generate/excel_analyze等
    - **instruction**: 任务指令
    - **file_ids**: 输入文件ID列表
    - **priority**: high/normal/low
    """
    from ...database.session import session_scope
    from ...database.repository import TaskRepository, FileRepository

    priority = (req.priority or "normal") if hasattr(req, "priority") else "normal"
    if priority not in ("high", "normal", "low"):
        priority = "normal"

    # 1. 创建数据库记录
    task_id = None
    input_files = []
    with session_scope() as session:
        task_repo = TaskRepository(session)
        file_repo = FileRepository(session)

        # 获取输入文件路径
        input_paths = []
        if req.file_ids:
            input_paths = _resolve_input_files(req.file_ids, file_repo)
            input_files = list(req.file_ids)

        db_task = task_repo.create_task(
            task_type=req.task_type,
            instruction=req.instruction,
            agent_name=req.task_type.split("_")[0] + "_agent" if "_" in req.task_type else None,
            input_file_ids=json.dumps(input_files) if input_files else None,
            options_json=json.dumps({
                "output_format": getattr(req, "output_format", None),
                "options": req.options or {},
                "input_paths": input_paths,
            }, ensure_ascii=False),
            priority={"high": 2, "normal": 1, "low": 0}.get(priority, 1),
        )
        task_id = db_task.id

    # 2. 提交到任务队列
    try:
        from ...task_queue import submit_task, init_worker, queue_name_for_task_type
        init_worker()

        queue_task_name = queue_name_for_task_type(req.task_type)
        options = req.options or {}
        input_path = input_paths[0] if input_paths else None

        submit_task(
            task_name=queue_task_name,
            kwargs={
                "input_path": input_path,
                "instruction": req.instruction,
                "options": {**options, "input_file_ids": input_files},
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
        logger.error(f"任务队列不可用: {e}", exc_info=True)
        with session_scope() as session:
            TaskRepository(session).fail_task(task_id, f"任务队列不可用: {e}")
        status = "failed"

    return BaseResponse(data=TaskInfo(
        task_id=task_id,
        task_type=req.task_type,
        agent=req.task_type.split("_")[0] + "_agent" if "_" in req.task_type else "",
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
                    input_files=_file_id_list_to_infos(json.loads(db_task.input_file_ids) if db_task.input_file_ids else [], file_repo),
                    output_files=_file_id_list_to_infos(json.loads(db_task.output_file_ids) if db_task.output_file_ids else [], file_repo),
                    result=_sanitize_result(json.loads(db_task.result_json)) if db_task.result_json else None,
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

    # 回退到内存
    task = task_manager.get_task(task_id)
    if task:
        return BaseResponse(data=TaskInfo(**task.to_dict()))

    raise TaskNotFoundError(f"任务不存在: {task_id}")


@router.get("/", response_model=BaseResponse[TaskListResponse],
            summary="任务列表")
async def list_tasks(status: str = None, agent: str = None,
                     page: int = 1, page_size: int = 20):
    """列出任务，支持按状态/Agent筛选"""
    session = _get_db_session()
    if session:
        try:
            from ...database.repository import TaskRepository, FileRepository
            repo = TaskRepository(session)
            file_repo = FileRepository(session)
            filters = {}
            if status:
                filters["status"] = status
            if agent:
                filters["agent_name"] = agent
            db_tasks = repo.find(offset=(page - 1) * page_size, limit=page_size, **filters)
            total = repo.count()
            task_infos = [TaskInfo(
                task_id=t.id, task_type=t.task_type, agent=t.agent_name or "",
                status=t.status, progress=t.progress or 0,
                current_step=t.current_step, instruction=t.instruction,
                input_files=_file_id_list_to_infos(json.loads(t.input_file_ids) if t.input_file_ids else [], file_repo),
                output_files=_file_id_list_to_infos(json.loads(t.output_file_ids) if t.output_file_ids else [], file_repo),
                result=_sanitize_result(json.loads(t.result_json)) if t.result_json else None,
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
    if session:
        try:
            from ...database.repository import TaskRepository
            repo = TaskRepository(session)
            if repo.get_by_id(task_id):
                repo.add_feedback(task_id, req.rating, req.comment)
                session.commit()
                return BaseResponse(message="反馈已提交", data={
                    "task_id": task_id, "rating": req.rating, "comment": req.comment,
                })
        finally:
            session.close()

    return BaseResponse(message="反馈已提交", data={
        "task_id": task_id, "rating": req.rating, "comment": req.comment,
    })
