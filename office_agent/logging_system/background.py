"""Bounded background writer used by database log handlers."""
import atexit
import logging
import queue
import threading
from typing import Callable


_jobs: queue.Queue = queue.Queue(maxsize=1000)
_sentinel = object()
_worker = None
_worker_lock = threading.Lock()

_logger = logging.getLogger("office_agent.logging.background")


def _run():
    while True:
        job = _jobs.get()
        try:
            if job is _sentinel:
                return
            func, args, kwargs = job
            try:
                func(*args, **kwargs)
            except Exception:
                # 单个写任务失败不得杀死唯一写线程：未捕获的异常会让线程
                # 静默退出（frozen 应用 stderr 为空流，异常完全不可见），
                # 队列中后续日志全部滞留。记录后继续消费，保持线程存活。
                _logger.exception("后台日志写任务失败，已跳过该任务")
        finally:
            _jobs.task_done()


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        with _worker_lock:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(
                    target=_run, name="office-agent-db-log-writer", daemon=True
                )
                _worker.start()


def submit(func: Callable, *args, **kwargs) -> bool:
    """Queue a write without spawning an unbounded number of threads."""
    _ensure_worker()
    try:
        _jobs.put_nowait((func, args, kwargs))
        return True
    except queue.Full:
        return False


def _drain_inline():
    """在当前线程同步排空队列中剩余任务（shutdown/队列满兜底，避免丢日志）。"""
    while True:
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            return
        try:
            if job is _sentinel:
                continue
            func, args, kwargs = job
            try:
                func(*args, **kwargs)
            except Exception:
                _logger.exception("shutdown 同步兜底写任务失败，已跳过")
        finally:
            _jobs.task_done()


def shutdown(timeout: float = 2.0):
    """Best-effort drain on interpreter shutdown."""
    worker = _worker
    if worker is None or not worker.is_alive():
        _drain_inline()
        return
    try:
        _jobs.put(_sentinel, timeout=timeout)
    except queue.Full:
        # P3-116: sentinel 进不去说明仍有最多 1000 条积压，本线程兜底排空而非丢弃
        _drain_inline()
        return
    worker.join(timeout=timeout)
    if worker.is_alive():
        _drain_inline()


atexit.register(shutdown)
