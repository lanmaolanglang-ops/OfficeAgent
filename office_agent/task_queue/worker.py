"""
Worker 抽象层

使用本地线程池 Worker（零外部依赖）。

统一接口：
    worker.submit(task_name, args, kwargs, priority, task_id)
    worker.get_status(task_id)
    worker.revoke(task_id)
"""
import os
import json
import uuid
import traceback
import threading
import logging
import time
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Dict

from .config import config
from .lease_executor import LeaseThreadPool

logger = logging.getLogger("office_agent.queue")


class TaskCancelledError(RuntimeError):
    """任务在协作式取消点中止。"""


class TaskProgress:
    """任务进度回调"""

    def __init__(self, task_id: str, session_factory=None,
                 cancel_event: threading.Event = None):
        self.task_id = task_id
        self.session_factory = session_factory
        self.cancel_event = cancel_event
        self._last_persisted_at = 0.0
        self._last_persisted_progress: int | None = None
        self._persist_lock = threading.Lock()

    def check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise TaskCancelledError(f"任务已取消: {self.task_id}")

    def update(self, progress: int, step: str = None):
        """更新进度到数据库"""
        self.check_cancelled()
        progress = max(0, min(100, int(progress)))
        now = time.monotonic()
        with self._persist_lock:
            should_persist = (
                progress == 100
                or self._last_persisted_progress is None
                or (progress != self._last_persisted_progress
                    and now - self._last_persisted_at >= 0.25)
            )
            if not should_persist:
                return
            self._last_persisted_at = now
            self._last_persisted_progress = progress
        if self.session_factory:
            session = None
            try:
                session = self.session_factory()
                from ..database.repository import TaskRepository
                repo = TaskRepository(session)
                repo.update_progress(self.task_id, progress, step)
                session.commit()
            except Exception as e:
                # 进度更新失败会让前端进度条卡住，必须可见而不是吞成 debug
                logger.warning("任务 %s 进度更新失败: %s", self.task_id, e)
            finally:
                if session is not None:
                    session.close()
        self.check_cancelled()


class LocalWorker:
    """
    本地线程池 Worker

    零依赖，适合开发和单机部署。
    支持优先级（通过不同的线程池）、重试、状态持久化。
    """

    def __init__(self, max_workers: int = None):
        # 三个优先级的线程池，并发数取自 TASK_QUEUES 配置（默认 high=2/normal=4/low=2）。
        # 使用 LeaseThreadPool：任务线程进入 failover 正常退避等可中断长等待时
        # 通过租约 park 让出并发额度（有界替补线程接管排队任务），唤醒后额度回落；
        # 取消/重试/冷却/备用模型语义不变。
        queues = config.TASK_QUEUES
        self.max_workers = sum(queue["concurrency"] for queue in queues.values())
        self.executors = {
            "high": LeaseThreadPool(max_workers=queues["high"]["concurrency"], thread_name_prefix="task-high"),
            "normal": LeaseThreadPool(max_workers=queues["normal"]["concurrency"], thread_name_prefix="task-normal"),
            "low": LeaseThreadPool(max_workers=queues["low"]["concurrency"], thread_name_prefix="task-low"),
        }
        # 收尾执行器：质量评分会同步重开 Office 文档、修订派发会同步调用 LLM，
        # 这些耗时工作不再占用优先级任务线程，统一在独立的有界线程上完成。
        self._finalize_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="task-finalize")
        # 任务注册表
        self._tasks: Dict[str, Callable] = {}
        # Future 映射
        self._futures: Dict[str, Future] = {}
        # 收尾 Future 映射（任务函数已返回、收尾仍在进行的任务）
        self._finalizing: Dict[str, Future] = {}
        # 取消标记
        self._cancelled: set = set()
        self._cancel_events: Dict[str, threading.Event] = {}
        # 软超时标记 + 计时器
        self._timed_out: set = set()
        self._timers: Dict[str, threading.Timer] = {}
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
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[task_id] = cancel_event

        # 状态更新为 queued
        self._update_status(task_id, "queued", progress=0)

        # 选择执行器
        executor = self.executors.get(priority, self.executors["normal"])

        # 成功收尾是否已卸载到收尾执行器。决定主 Future 的清理回调是否
        # 需要把共享状态（取消事件/计时器等）留给收尾完成回调清理。
        finalize_offloaded = False

        def _run():
            nonlocal finalize_offloaded
            # 排队期间已取消的任务不要设置线程上下文；旧实现会在这里提前
            # return 而跳过 reset，导致同一工作线程的后续日志继承错误 task_id。
            if task_id in self._cancelled:
                self._update_status(task_id, "cancelled")
                return {"status": "cancelled"}

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

            self._update_status(task_id, "running", progress=0)
            progress_cb = TaskProgress(task_id, self._session_factory, cancel_event)

            # 软超时计时器：运行超过阈值则标记失败（不杀线程）
            timer = None
            if config.TASK_SOFT_TIMEOUT and config.TASK_SOFT_TIMEOUT > 0:
                timer = threading.Timer(config.TASK_SOFT_TIMEOUT, self._on_task_timeout, args=(task_id,))
                timer.daemon = True
                timer.start()
                with self._lock:
                    self._timers[task_id] = timer

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
                    registry.gauge("tasks_active").dec(task_type=task_name)
                    self._update_status(task_id, "cancelled")
                    return {"status": "cancelled"}
                # 任务执行期间超时：标记失败（不覆盖为 completed）
                if task_id in self._timed_out:
                    registry.gauge("tasks_active").dec(task_type=task_name)
                    registry.counter("tasks_total").inc(
                        task_type=task_name, status="failed"
                    )
                    log_task_event(task_id, "failed", "error",
                                  details={"error": "任务执行超时", "duration_ms": int(duration * 1000)})
                    return {"status": "failed", "error": "任务执行超时"}
                # 任务函数可通过返回 {"status":"failed"} 表示失败（保留函数内
                # sanitize 后的 error 与 model_call 元数据），不必依赖抛异常。
                if isinstance(result, dict) and result.get("status") == "failed":
                    error_msg = result.get("error") or "任务处理失败"
                    _task_logger.error(f"任务 {task_id} 失败: {error_msg}")
                    self._fail(task_id, error_msg, int(duration * 1000))
                    log_task_event(task_id, "failed", "error",
                                  details={"error": error_msg, "duration_ms": int(duration * 1000)})
                    registry.counter("tasks_total").inc(
                        task_type=task_name, status="failed"
                    )
                    registry.gauge("tasks_active").dec(task_type=task_name)
                    return result
                # 成功收尾（评分→修订派发→success 落库）提交到收尾执行器，
                # 任务线程立即归还优先级线程池；执行器已关闭（进程退出竞态）
                # 时内联回退，行为与旧版一致。
                try:
                    finalize_future = self._finalize_executor.submit(
                        self._finalize_success, task_id, task_name,
                        result, duration)
                except RuntimeError:
                    finalize_future = None
                if finalize_future is None:
                    self._finalize_success(task_id, task_name, result, duration)
                else:
                    finalize_offloaded = True
                    with self._lock:
                        self._finalizing[task_id] = finalize_future

                    def _finalize_cleanup(_future):
                        with self._lock:
                            self._finalizing.pop(task_id, None)
                            timer = self._timers.pop(task_id, None)
                            self._cancelled.discard(task_id)
                            self._timed_out.discard(task_id)
                            self._cancel_events.pop(task_id, None)
                        if timer:
                            timer.cancel()

                    finalize_future.add_done_callback(_finalize_cleanup)
                return result
            except TaskCancelledError:
                registry.gauge("tasks_active").dec(task_type=task_name)
                self._update_status(task_id, "cancelled")
                log_task_event(task_id, "cancelled", "cancelled")
                return {"status": "cancelled"}
            except Exception as e:
                duration = time.time() - start_time
                from ..security.error_sanitizer import sanitize_error
                error_msg = sanitize_error(e)
                tb = traceback.format_exc()
                _task_logger.error(f"任务 {task_id} 失败: {error_msg}\n{tb}")
                self._fail(task_id, error_msg, int(duration * 1000))
                log_task_event(task_id, "failed", "error",
                              details={"error": error_msg, "duration_ms": int(duration * 1000)})
                registry.counter("tasks_total").inc(
                    task_type=task_name, status="failed"
                )
                registry.gauge("tasks_active").dec(task_type=task_name)
                return {"status": "failed", "error": error_msg}
            finally:
                with self._lock:
                    timer = self._timers.pop(task_id, None)
                if timer:
                    timer.cancel()
                from ..logging_system.context import _request_id_var, _task_id_var
                _request_id_var.reset(req_token)
                _task_id_var.reset(task_token)

        future = executor.submit(_run)
        with self._lock:
            self._futures[task_id] = future

        # Future 可能在 submit 返回后、写入映射前就已完成。done callback 在
        # 已完成 Future 上会立即执行，因此无论快任务、取消还是超时都能清理。
        def _cleanup(_future):
            timer = None
            with self._lock:
                self._futures.pop(task_id, None)
                if finalize_offloaded:
                    # 收尾仍在收尾执行器上进行：取消事件等共享状态由
                    # 收尾完成回调清理，否则修订派发读不到取消信号。
                    return
                timer = self._timers.pop(task_id, None)
                self._cancelled.discard(task_id)
                self._timed_out.discard(task_id)
                self._cancel_events.pop(task_id, None)
            if timer:
                timer.cancel()

        future.add_done_callback(_cleanup)

        return task_id

    def get_active_count(self) -> int:
        """返回仍在排队、执行或收尾中的本地任务数。"""
        with self._lock:
            running = sum(1 for future in self._futures.values() if not future.done())
            finalizing = sum(1 for future in self._finalizing.values() if not future.done())
            return running + finalizing

    # 终态集合：一旦写入，不允许被软超时/取消等回调改写
    _TERMINAL_STATUSES = ("success", "failed", "cancelled")

    def _update_status(self, task_id: str, status: str, progress: int = None,
                       step: str = None, error: str = None,
                       duration_ms: int = None):
        """更新任务状态到数据库"""
        if not self._session_factory:
            return
        session = None
        try:
            session = self._session_factory()
            from ..database.repository import TaskRepository
            repo = TaskRepository(session)
            current = repo.get_by_id(task_id)
            if current is not None and current.status in self._TERMINAL_STATUSES:
                if current.status != status:
                    # 终态不可覆盖：防止软超时回调把已成功的任务改写为 failed
                    logger.debug("任务 %s 已处于终态 %s，忽略状态更新 %s",
                                 task_id, current.status, status)
                    return
            if current is None and status == "running" and progress == 0:
                # 任务可能还没创建，跳过
                return
            data = {}
            if status not in self._TERMINAL_STATUSES and status != "running":
                data["status"] = status
            if progress is not None and status not in self._TERMINAL_STATUSES:
                data["progress"] = progress
            if step is not None and status not in self._TERMINAL_STATUSES:
                data["current_step"] = step
            if error is not None and status not in self._TERMINAL_STATUSES:
                data["error_message"] = error
            if duration_ms is not None and status not in self._TERMINAL_STATUSES:
                data["duration_ms"] = duration_ms
            if status == "running":
                repo.start_task(task_id)
                data.pop("status", None)
            if data:
                repo.update(task_id, data)
            if status == "success":
                repo.complete_task(task_id, duration_ms=duration_ms)
            elif status == "failed":
                repo.fail_task(task_id, error or "未知错误", duration_ms=duration_ms)
            elif status == "cancelled":
                repo.cancel_task(task_id)
            session.commit()
        except Exception as e:
            logger.warning("任务 %s 状态更新为 %s 失败: %s", task_id, status, e)
        finally:
            if session is not None:
                session.close()

    @staticmethod
    def _looks_like_path(value: Any) -> bool:
        """Best-effort check that a value is not a local filesystem path."""
        if not isinstance(value, str):
            return False
        import re as _re
        # 任意盘符（不限 C/D），或包含路径分隔符 / 以 ~ . 开头
        if _re.match(r"^[a-zA-Z]:[\\/]", value):
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

    def _finalize_success(self, task_id: str, task_name: str,
                          result: Any, duration: float):
        """成功收尾：质量评分 → 修订派发 → success 落库 → 日志/指标。

        在独立的收尾执行器线程上运行（执行器已关闭时由内联回退调用）。
        收尾会同步重开 Office 文档、可能同步调用 LLM，绝不能占用优先级
        任务线程。异常语义与旧内联实现一致：收尾自身失败时走失败落库
        （终态守卫保证已写入的 success 不被覆盖），tasks_active 一定归还。
        """
        from ..logging_system.logger import log_task_event
        from ..logging_system.metrics import registry
        try:
            self._complete(task_id, result, int(duration * 1000))
            log_task_event(task_id, "completed", "success",
                           details={"duration_ms": int(duration * 1000)})
            registry.counter("tasks_total").inc(
                task_type=task_name, status="success"
            )
            registry.histogram("task_duration_seconds").observe(
                duration, task_type=task_name
            )
        except Exception as e:
            from ..security.error_sanitizer import sanitize_error
            logger.exception("任务 %s 成功收尾失败", task_id)
            self._fail(task_id, sanitize_error(e), int(duration * 1000))
        finally:
            registry.gauge("tasks_active").dec(task_type=task_name)

    def _complete(self, task_id: str, result: Any, duration_ms: int = None):
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
            session = None
            try:
                session = self._session_factory()
                from ..database.repository import TaskRepository
                repo = TaskRepository(session)
                current_task = repo.get_by_id(task_id)
                if current_task is not None and current_task.status in self._TERMINAL_STATUSES:
                    # 已被软超时/取消回调标记为终态：不覆盖结果，也不再派发修订
                    logger.info("任务 %s 已处于终态 %s，跳过完成写入",
                                task_id, current_task.status)
                    session.close()
                    session = None
                else:
                    # 质量修订子任务必须在写 success 之前创建并合入 result：
                    # 若先提交 success，前端轮询到 completed 即停止轮询，
                    # 永远看不到 auto_revision_task_id，修订链在 UI 上失效。
                    if (isinstance(result, dict)
                            and result.get("quality_check", {}).get("passed") is False):
                        try:
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
                                result_json = json.dumps(
                                    result, ensure_ascii=False, default=str)
                        except Exception:
                            # 修订派发失败不影响任务本身的成功落库
                            logger.exception("质量修订子任务创建失败: %s", task_id)
                    repo.complete_task(task_id, result_json=result_json,
                                       output_file_ids=output_file_ids,
                                       quality_score=quality_score,
                                       duration_ms=duration_ms)
                    session.commit()
            except Exception:
                logger.exception("failed to persist task completion metadata: %s", task_id)
            finally:
                if session is not None:
                    session.close()

    def _schedule_quality_revision(self, task_id: str, task, result: dict,
                                   output_ids: list):
        """Create a bounded child task when the generated artifact fails QA."""
        with self._lock:
            cancel_event = self._cancel_events.get(task_id)
        if cancel_event is not None and cancel_event.is_set():
            return None
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
            response = ModelGateway(cancel_event=cancel_event).chat(
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
        if cancel_event is not None and cancel_event.is_set():
            return None
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

    def _fail(self, task_id: str, error: str, duration_ms: int = None):
        """任务失败"""
        self._update_status(task_id, "failed", progress=100,
                            step="处理失败", error=error,
                            duration_ms=duration_ms)

    def _on_task_timeout(self, task_id: str):
        """软超时回调：任务仍运行时标记失败（线程继续自然结束，不杀线程）"""
        with self._lock:
            future = self._futures.get(task_id)
            if future and not future.done():
                self._timed_out.add(task_id)
            else:
                return
        self._update_status(task_id, "failed", progress=100,
                            step="处理超时", error="任务执行超时")

    def revoke(self, task_id: str):
        """取消任务"""
        with self._lock:
            future = self._futures.get(task_id)
            finalize_future = self._finalizing.get(task_id)
            running = future is not None and not future.done()
            finalizing = finalize_future is not None and not finalize_future.done()
            if running or finalizing:
                # 收尾阶段取消同样要置取消事件：修订派发在收尾线程上检查
                # 该事件，置位后不再创建质量修订子任务。
                self._cancelled.add(task_id)
                cancel_event = self._cancel_events.get(task_id)
                if cancel_event:
                    cancel_event.set()
                if future is not None:
                    future.cancel()
        self._update_status(task_id, "cancelled")

    def shutdown(self, wait: bool = True):
        """关闭所有线程池"""
        for executor in self.executors.values():
            executor.shutdown(wait=wait)
        self._finalize_executor.shutdown(wait=wait)
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
