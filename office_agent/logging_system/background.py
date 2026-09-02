"""Bounded background writer used by database log handlers."""
import atexit
import queue
import threading
from typing import Callable


_jobs: queue.Queue = queue.Queue(maxsize=1000)
_sentinel = object()
_worker = None
_worker_lock = threading.Lock()


def _run():
    while True:
        job = _jobs.get()
        try:
            if job is _sentinel:
                return
            func, args, kwargs = job
            func(*args, **kwargs)
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


def shutdown(timeout: float = 2.0):
    """Best-effort drain on interpreter shutdown."""
    worker = _worker
    if worker is None or not worker.is_alive():
        return
    try:
        _jobs.put(_sentinel, timeout=timeout)
    except queue.Full:
        return
    worker.join(timeout=timeout)


atexit.register(shutdown)
