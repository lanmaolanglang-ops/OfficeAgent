"""
Office Agent Task Queue - 异步任务系统

本地线程池任务系统（零外部依赖）。

使用示例：
    from office_agent.task_queue import submit_task, get_worker

    # 提交任务
    task_id = submit_task(
        "word.format",
        kwargs={"input_path": "/path/to/doc.docx", "instruction": "排版"},
        priority="high",
    )

    # 查询状态
    from office_agent.database.repository import TaskRepository
    with session_scope() as s:
        task = TaskRepository(s).get_by_id(task_id)
        print(task.status, task.progress)
"""
from .config import config
from .worker import get_worker, LocalWorker
from .tasks import TASK_REGISTRY, TASK_TYPE_TO_QUEUE, queue_name_for_task_type
from .._version import __version__

def submit_task(task_name: str, args: tuple = (), kwargs: dict = None,
                priority: str = "normal", task_id: str = None,
                task_type: str = None, instruction: str = None,
                user_id: str = None) -> str:
    """
    提交任务到队列

    Args:
        task_name: 任务名（如 word.format, ppt.generate）
        args: 位置参数
        kwargs: 关键字参数
        priority: high/normal/low
        task_id: 指定任务ID（通常由数据库创建后传入）
        task_type: 任务类型（用于数据库记录）
        instruction: 任务指令
        user_id: 用户ID

    Returns:
        task_id
    """
    from ..database.session import session_scope
    from ..database.repository import TaskRepository
    import json

    worker = get_worker()

    # 如果没有传入 task_id，先在数据库创建记录
    if not task_id:
        with session_scope() as session:
            repo = TaskRepository(session)
            db_task = repo.create_task(
                task_type=task_type or task_name,
                instruction=instruction or task_name,
                agent_name=task_name.split(".")[0] + "_agent" if "." in task_name else None,
                user_id=user_id,
                priority={"high": 2, "normal": 1, "low": 0}.get(priority, 1),
                options_json=json.dumps({"task_name": task_name}, ensure_ascii=False),
            )
            task_id = db_task.id

    # 注册任务（LocalWorker 需要）
    if isinstance(worker, LocalWorker):
        for name, func in TASK_REGISTRY.items():
            worker.register(name, func)

    # 提交到队列
    worker.submit(
        task_name=task_name,
        args=args,
        kwargs=kwargs or {},
        priority=priority,
        task_id=task_id,
    )

    return task_id


def cancel_task(task_id: str) -> bool:
    """取消任务"""
    worker = get_worker()
    worker.revoke(task_id)
    return True


def init_worker():
    """初始化 Worker 并注册所有任务"""
    worker = get_worker()
    if isinstance(worker, LocalWorker):
        for name, func in TASK_REGISTRY.items():
            worker.register(name, func)
    return worker


__all__ = [
    "__version__",
    "config", "get_worker", "LocalWorker",
    "submit_task", "cancel_task", "init_worker", "TASK_REGISTRY",
    "TASK_TYPE_TO_QUEUE", "queue_name_for_task_type",
]
