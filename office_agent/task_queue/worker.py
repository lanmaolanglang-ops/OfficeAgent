"""
Worker 抽象层

使用本地线程池 Worker（零外部依赖）。

统一接口：
    worker.submit(task_name, args, kwargs, priority, task_id)
    worker.get_status(task_id)
    worker.revoke(task_id)
"""
import os
import sys
import json
import uuid
import traceback
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from .config import config

logger = logging.getLogger("office_agent.queue")


class TaskProgress:
    """任务进度回调"""

    def __init__(self, task_id: str, session_factory=None):
        self.task_id = task_id
        self.session_factory = session_factory

    def update(self, progress: int, step: str = None):
        """更新进度到数据库"""
        if self.session_factory:
            try:
                session = self.session_factory()
                from ..database.repository import TaskRepository
                repo = TaskRepository(session)
                repo.update_progress(self.task_id, progress, step)
                session.commit()
                session.close()
            except Exception as e:
                logger.debug(f"进度更新失败: {e}")


class LocalWorker:
    """
    本地线程池 Worker

    零依赖，适合开发和单机部署。
    支持优先级（通过不同的线程池）、重试、状态持久化。
    """

    def __init__(self, max_workers: int = None):
        self.max_workers = max_workers or config.LOCAL_MAX_WORKERS
        # 三个优先级的线程池，并发数取自 TASK_QUEUES 配置（默认 high=2/normal=4/low=2）
        queues = config.TASK_QUEUES
        self.executors = {
            "high": ThreadPoolExecutor(max_workers=queues["high"]["concurrency"], thread_name_prefix="task-high"),
            "normal": ThreadPoolExecutor(max_workers=queues["normal"]["concurrency"], thread_name_prefix="task-normal"),
            "low": ThreadPoolExecutor(max_workers=queues["low"]["concurrency"], thread_name_prefix="task-low"),
        }
        # 任务注册表
        self._tasks: Dict[str, Callable] = {}
        # Future 映射
        self._futures: Dict[str, Future] = {}
        # 取消标记
        self._cancelled: set = set()
        self._lock = threading.RLock()

        # 数据库 session 工厂
        self._session_factory = None
        try:
            from ..database.session import SessionLocal
            self._session_factory = SessionLocal
        except Exception:
            pass

        logger.info(f"LocalWorker 初始化完成，总并发数: {sum(q['concurrency'] for q in config.TASK_QUEUES.values())}")

    def register(self, name: str, func: Callable):
        """注册任务函数"""
        self._tasks[name] = func
        logger.debug(f"注册任务: {name}")

    def submit(self, task_name: str, args: tuple = (), kwargs: dict = None,
               priority: str = "normal", task_id: str = None) -> str:
        """提交任务"""
        with self._lock:
            task_exists = task_name in self._tasks
        if not task_exists:
            raise ValueError(f"未注册的任务: {task_name}")

        task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        kwargs = kwargs or {}
        kwargs["_task_id"] = task_id
        kwargs["_priority"] = priority

        # 状态更新为 queued
        self._update_status(task_id, "queued", progress=0)

        # 选择执行器
        executor = self.executors.get(priority, self.executors["normal"])

        def _run():
            # 设置日志上下文
            from ..logging_system.context import set_task_id, set_request_id, generate_request_id
            from ..logging_system.logger import log_task_event, get_logger
            from ..logging_system.metrics import registry
            from ..logging_system.tracer import trace_task
            import time

            _task_logger = get_logger("task")
            req_token = set_request_id(generate_request_id())
            task_token = set_task_id(task_id)
            start_time = time.time()

            if task_id in self._cancelled:
                self._update_status(task_id, "cancelled")
                return {"status": "cancelled"}

            self._update_status(task_id, "running", progress=0)
            progress_cb = TaskProgress(task_id, self._session_factory)

            log_task_event(task_id, "started", "running")
            registry.counter("tasks_total").inc(
                task_type=task_name, status="started"
            )
            registry.gauge("tasks_active").inc(task_type=task_name)

            try:
                kwargs["progress"] = progress_cb
                with trace_task(task_id, task_name):
                    with self._lock:
                        task_func = self._tasks[task_name]
                    result = task_func(*args, **kwargs)
                duration = time.time() - start_time
                # 任务执行期间被取消：不再把状态覆盖为 completed/failed
                if task_id in self._cancelled:
                    self._update_status(task_id, "cancelled")
                    return {"status": "cancelled"}
                # 任务函数可通过返回 {"status":"failed"} 表示失败（保留函数内
                # sanitize 后的 error 与 model_call 元数据），不必依赖抛异常。
                if isinstance(result, dict) and result.get("status") == "failed":
                    error_msg = result.get("error") or "任务处理失败"
                    _task_logger.error(f"任务 {task_id} 失败: {error_msg}")
                    self._fail(task_id, error_msg)
                    log_task_event(task_id, "failed", "error",
                                  details={"error": error_msg, "duration_ms": int(duration * 1000)})
                    registry.counter("tasks_total").inc(
                        task_type=task_name, status="failed"
                    )
                    registry.gauge("tasks_active").dec(task_type=task_name)
                    return result
                self._complete(task_id, result)
                log_task_event(task_id, "completed", "success",
                              details={"duration_ms": int(duration * 1000)})
                registry.counter("tasks_total").inc(
                    task_type=task_name, status="success"
                )
                registry.histogram("task_duration_seconds").observe(
                    duration, task_type=task_name
                )
                registry.gauge("tasks_active").dec(task_type=task_name)
                return result
            except Exception as e:
                duration = time.time() - start_time
                error_msg = f"{type(e).__name__}: {str(e)}"
                tb = traceback.format_exc()
                _task_logger.error(f"任务 {task_id} 失败: {error_msg}\n{tb}")
                self._fail(task_id, error_msg)
                log_task_event(task_id, "failed", "error",
                              details={"error": error_msg, "duration_ms": int(duration * 1000)})
                registry.counter("tasks_total").inc(
                    task_type=task_name, status="failed"
                )
                registry.gauge("tasks_active").dec(task_type=task_name)
                return {"status": "failed", "error": error_msg}
            finally:
                from ..logging_system.context import _request_id_var, _task_id_var
                _request_id_var.reset(req_token)
                _task_id_var.reset(task_token)

        future = executor.submit(_run)
        with self._lock:
            self._futures[task_id] = future

        return task_id

    def _update_status(self, task_id: str, status: str, progress: int = None,
                       step: str = None, error: str = None):
        """更新任务状态到数据库"""
        if not self._session_factory:
            return
        try:
            session = self._session_factory()
            from ..database.repository import TaskRepository
            repo = TaskRepository(session)
            data = {"status": status}
            if progress is not None:
                data["progress"] = progress
            if step is not None:
                data["current_step"] = step
            if error is not None:
                data["error_message"] = error
            if status == "running" and not repo.get_by_id(task_id) and progress == 0:
                # 任务可能还没创建，跳过
                pass
            else:
                repo.update(task_id, data)
                if status == "success":
                    repo.complete_task(task_id)
                elif status == "failed":
                    repo.fail_task(task_id, error or "未知错误")
                elif status == "cancelled":
                    repo.cancel_task(task_id)
            session.commit()
            session.close()
        except Exception as e:
            logger.debug(f"状态更新失败: {e}")

    @staticmethod
    def _looks_like_path(value: Any) -> bool:
        """Best-effort check that a value is not a local filesystem path."""
        if not isinstance(value, str):
            return False
        low = value.lower()
        if "c:\\" in low or "d:\\" in low:
            return True
        if "\\" in value or "/" in value:
            return True
        if value.startswith("~") or value.startswith("."):
            return True
        return False

    @staticmethod
    def _valid_output_file_ids(output_files: Any) -> tuple:
        """Return (valid_ids, skipped) where valid_ids are file_id strings only."""
        if not isinstance(output_files, list):
            return [], []
        valid = []
        skipped = []
        for item in output_files:
            if isinstance(item, str) and item.startswith("file_") and not LocalWorker._looks_like_path(item):
                valid.append(item)
            else:
                skipped.append(item)
        return valid, skipped

    def _complete(self, task_id: str, result: Any):
        """任务完成"""
        result_json = None
        output_file_ids = None
        quality_score = None
        if isinstance(result, dict):
            result_json = json.dumps(result, ensure_ascii=False, default=str)
            valid_ids, skipped = self._valid_output_file_ids(result.get("output_files"))
            if skipped:
                logger.warning(f"任务 {task_id} 有 {len(skipped)} 个无效 file_id 已跳过: {skipped}")
            output_file_ids = json.dumps(valid_ids, ensure_ascii=False) if valid_ids else None
            if valid_ids:
                try:
                    from ..storage.storage_service import get_storage_service
                    from ..quality_scoring.scoring_engine import QualityScoringEngine
                    storage = get_storage_service()
                    reports = []
                    scorer = QualityScoringEngine()
                    for file_id in valid_ids:
                        path = storage.get_file_path(file_id)
                        if path and os.path.isfile(path) and os.path.splitext(path)[1].lower() in {
                            ".docx", ".pptx", ".xlsx"
                        }:
                            reports.append(scorer.score_file(path).to_dict())
                    if reports:
                        quality_score = min(r.get("total_score", 0.0) for r in reports)
                        result["quality_check"] = {
                            "passed": all(r.get("passed", False) for r in reports),
                            "score": quality_score,
                            "reports": reports,
                        }
                        result_json = json.dumps(result, ensure_ascii=False, default=str)
                except Exception as exc:
                    logger.warning("quality check skipped for task %s: %s", task_id, exc)

        if self._session_factory:
            try:
                session = self._session_factory()
                from ..database.repository import TaskRepository
                repo = TaskRepository(session)
                current_task = repo.get_by_id(task_id)
                repo.complete_task(task_id, result_json=result_json,
                                   output_file_ids=output_file_ids,
                                   quality_score=quality_score)
                session.commit()
                session.close()
                child_id = None
                if (isinstance(result, dict)
                        and result.get("quality_check", {}).get("passed") is False):
                    child_id = self._schedule_quality_revision(
                        task_id, current_task, result, valid_ids or []
                    )
                    if child_id:
                        result["auto_revision_task_id"] = child_id
                        result["quality_revision"] = {
                            "task_id": child_id,
                            "issues": result.get("quality_check", {}).get("reports", []),
                            "model_call": result.get("quality_revision_model_call"),
                        }
                        session = self._session_factory()
                        from ..database.repository import TaskRepository
                        TaskRepository(session).update(task_id, {
                            "result_json": json.dumps(result, ensure_ascii=False, default=str)
                        })
                        session.commit()
                        session.close()
            except Exception:
                logger.exception("failed to persist task completion metadata: %s", task_id)

        with self._lock:
            self._futures.pop(task_id, None)

    def _schedule_quality_revision(self, task_id: str, task, result: dict,
                                   output_ids: list):
        """Create a bounded child task when the generated artifact fails QA."""
        if not task or (task.revision_number or 1) >= 3:
            return None
        try:
            options = json.loads(task.options_json or "{}")
        except (TypeError, ValueError):
            return None
        issues = []
        for report in result.get("quality_check", {}).get("reports", []):
            issues.extend(report.get("issues", [])[:5])
        if not issues or not output_ids:
            return None
        from ..storage.storage_service import get_storage_service
        input_path = get_storage_service().get_file_path(output_ids[0])
        if not input_path or not os.path.isfile(input_path):
            return None
        instruction = f"自动质量修订：基于原始要求‘{task.instruction}’，修复：" + "；".join(map(str, issues))
        llm_instruction = instruction
        model_call = {"called": False, "success": False, "fallback_used": True}
        try:
            from ..model_gateway import ModelGateway
            response = ModelGateway().chat(
                user_message=json.dumps({
                    "original_instruction": task.instruction,
                    "quality_issues": issues,
                    "history": options.get("history", [])[-10:],
                }, ensure_ascii=False),
                system_prompt=("你是办公文件质量修订规划器。根据原始要求和质量问题，生成一条完整可执行的修订指令。"
                               "保留原始目标和约束，只输出修订指令。"),
                task_type_str="simple_text", temperature=0.1, max_tokens=1200,
            )
            model_call = {"called": True, "success": bool(response.success),
                          "model": getattr(response, "model_used", None),
                          "provider": getattr(response, "provider", None),
                          "fallback_used": not bool(response.success)}
            if response.success and response.content and response.content.strip():
                llm_instruction = response.content.strip()
        except Exception as exc:
            logger.warning("质量修订LLM调用失败，使用规则修订指令: %s", exc)
        result["quality_revision_model_call"] = model_call
        instruction = llm_instruction
        child_options = {**options, "input_file_ids": output_ids,
                         "previous_instruction": task.instruction,
                         "is_follow_up": True, "revision_mode": "modify",
                         "quality_revision": True, "quality_issues": issues,
                         "history": (options.get("history", []) + [{"role": "user", "content": task.instruction}])[-10:]}
        from ..database.session import session_scope
        from ..database.repository import TaskRepository
        with session_scope() as session:
            child = TaskRepository(session).create_task(
                task_type=task.task_type, instruction=instruction,
                agent_name=task.agent_name, input_file_ids=json.dumps(output_ids),
                options_json=json.dumps(child_options, ensure_ascii=False),
                priority=task.priority, parent_task_id=task.id,
                revision_number=(task.revision_number or 1) + 1)
            child_id = child.id
        from . import submit_task
        from .tasks import queue_name_for_task_type
        queue_name = queue_name_for_task_type(task.task_type)
        submit_task(queue_name, kwargs={"input_path": input_path,
                     "instruction": instruction, "options": child_options},
                    priority="normal", task_id=child_id, task_type=task.task_type,
                    instruction=instruction)
        return child_id

    def _fail(self, task_id: str, error: str):
        """任务失败"""
        with self._lock:
            self._futures.pop(task_id, None)
        self._update_status(task_id, "failed", progress=100,
                            step="处理失败", error=error)

    def revoke(self, task_id: str):
        """取消任务"""
        with self._lock:
            self._cancelled.add(task_id)
            future = self._futures.get(task_id)
            if future and not future.done():
                future.cancel()
        self._update_status(task_id, "cancelled")

    def shutdown(self, wait: bool = True):
        """关闭所有线程池"""
        for executor in self.executors.values():
            executor.shutdown(wait=wait)
        logger.info("LocalWorker 已关闭")


# 全局 Worker 实例
_worker_instance = None
_worker_lock = threading.Lock()


def get_worker():
    """获取全局 Worker 实例（单例）"""
    global _worker_instance
    if _worker_instance is None:
        with _worker_lock:
            if _worker_instance is None:
                _worker_instance = LocalWorker()
    return _worker_instance
