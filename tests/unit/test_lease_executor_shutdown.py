"""P2-10：LeaseThreadPool 有界关闭回归。

修复前：``shutdown(wait=True)`` 的 join 循环无超时——任何卡住的任务
（阻塞 IO、挂死的外部调用）都会把应用退出流程永久挂起；排队任务在
shutdown 后仍会全部执行；超时后也无从诊断是哪些线程卡住。
"""
import threading
import time

import pytest

from office_agent.task_queue.lease_executor import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    LeaseThreadPool,
)


def test_shutdown_with_no_tasks_returns_immediately():
    pool = LeaseThreadPool(max_workers=2)
    started = time.monotonic()
    result = pool.shutdown(wait=True, timeout=5)
    assert result["timed_out"] is False
    assert result["alive_threads"] == []
    assert result["queued"] == 0
    assert time.monotonic() - started < 2


def test_running_task_completes_within_timeout():
    pool = LeaseThreadPool(max_workers=1)
    done = threading.Event()

    def quick():
        time.sleep(0.2)
        done.set()
        return "ok"

    future = pool.submit(quick)
    result = pool.shutdown(wait=True, timeout=5)
    assert done.is_set()
    assert future.result(timeout=0) == "ok"
    assert result["timed_out"] is False


def test_stuck_task_cannot_block_shutdown_forever():
    """卡住的任务超时后必须放行关闭流程，并给出可诊断状态。"""
    pool = LeaseThreadPool(max_workers=1, shutdown_timeout=0.3)
    release = threading.Event()

    def stuck():
        release.wait(timeout=10)  # 模拟挂死的任务

    pool.submit(stuck)
    time.sleep(0.1)  # 让任务进入运行态
    started = time.monotonic()
    result = pool.shutdown(wait=True)
    elapsed = time.monotonic() - started
    try:
        assert result["timed_out"] is True
        assert result["alive_threads"], "超时必须报告卡住的线程"
        assert elapsed < 5, "关闭等待必须被超时兜住，不能无限阻塞"
    finally:
        release.set()  # 释放卡住的线程，避免线程泄漏


def test_shutdown_rejects_new_submissions():
    pool = LeaseThreadPool(max_workers=1)
    pool.shutdown(wait=False)
    with pytest.raises(RuntimeError):
        pool.submit(lambda: 1)


def test_cancel_futures_drops_queued_tasks():
    pool = LeaseThreadPool(max_workers=1)
    gate = threading.Event()
    ran = []

    def blocker():
        gate.wait(timeout=5)

    def queued():
        ran.append(1)

    pool.submit(blocker)
    pending = pool.submit(queued)
    result = pool.shutdown(wait=True, cancel_futures=True, timeout=2)
    try:
        assert pending.cancelled()
        assert ran == []
        assert result["queued"] == 0
    finally:
        gate.set()


def test_shutdown_is_idempotent():
    pool = LeaseThreadPool(max_workers=1)
    first = pool.shutdown(wait=True, timeout=1)
    second = pool.shutdown(wait=True, timeout=1)
    assert first["timed_out"] is False
    assert second["timed_out"] is False


def test_wait_false_returns_without_waiting():
    pool = LeaseThreadPool(max_workers=1)
    release = threading.Event()

    def stuck():
        release.wait(timeout=5)

    pool.submit(stuck)
    started = time.monotonic()
    result = pool.shutdown(wait=False)
    try:
        assert result["timed_out"] is False
        assert time.monotonic() - started < 1
    finally:
        release.set()


def test_task_exception_does_not_hang_shutdown():
    """任务抛异常（worker 正常记账后退出）不阻塞关闭。"""
    pool = LeaseThreadPool(max_workers=1)

    def boom():
        raise ValueError("task failed")

    future = pool.submit(boom)
    result = pool.shutdown(wait=True, timeout=5)
    assert result["timed_out"] is False
    with pytest.raises(ValueError):
        future.result(timeout=0)


def test_default_timeout_is_module_constant():
    pool = LeaseThreadPool(max_workers=1)
    assert pool._shutdown_timeout == DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
