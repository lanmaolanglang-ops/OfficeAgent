"""Failover 正常退避通过租约让出 worker 并发额度的专项回归。

覆盖：
- LeaseThreadPool 准入规则（active < base + parked）与替补线程生命周期
- 停靠期间排队任务立即获得线程；无停靠时严格按并发数串行
- 取消事件立即唤醒停靠线程（语义与旧 cancel_event.wait 一致）
- failover 经 worker 线程退避时释放额度，重试/冷却/备用模型语义不变
"""
import threading
import time
from types import SimpleNamespace

import pytest

from office_agent.task_queue.lease_executor import LeaseThreadPool


def test_queued_task_runs_while_peer_is_parked():
    pool = LeaseThreadPool(max_workers=1, thread_name_prefix="t-park")
    parked = threading.Event()
    release = threading.Event()
    order = []

    def long_task():
        from office_agent.thread_lease import current_lease
        parked.set()
        # 模拟 failover 正常退避：停靠让出额度
        current_lease().park(0.4)
        order.append("long-resumed")
        release.wait(2)
        return "long"

    def queued_task():
        order.append("queued-ran")
        release.set()
        return "queued"

    try:
        fut_long = pool.submit(long_task)
        assert parked.wait(2)
        started = time.monotonic()
        fut_queued = pool.submit(queued_task)
        assert fut_queued.result(timeout=2) == "queued"
        # 停靠期间排队任务立即运行，不等 0.4s 退避结束
        assert time.monotonic() - started < 0.35
        assert fut_long.result(timeout=2) == "long"
        assert order == ["queued-ran", "long-resumed"]
    finally:
        pool.shutdown()


def test_without_park_tasks_serialize():
    pool = LeaseThreadPool(max_workers=1, thread_name_prefix="t-serial")
    first_done = threading.Event()
    overlapped = []

    def first():
        time.sleep(0.25)
        first_done.set()

    def second():
        overlapped.append(not first_done.is_set())

    try:
        fut1 = pool.submit(first)
        fut2 = pool.submit(second)
        fut1.result(timeout=2)
        fut2.result(timeout=2)
        assert overlapped == [False]
    finally:
        pool.shutdown()


def test_admission_never_exceeds_base_plus_parked():
    pool = LeaseThreadPool(max_workers=1, thread_name_prefix="t-cap")
    a_parked = threading.Event()
    b_running = threading.Event()
    c_started_before_slot = []

    def task_a():
        from office_agent.thread_lease import current_lease
        a_parked.set()
        current_lease().park(0.4)

    def task_b():
        b_running.set()
        time.sleep(0.6)

    def task_c():
        # C 只能在 A 唤醒（额度回落）或 B 完成后获得准入
        c_started_before_slot.append(a_parked.is_set()
                                     and b_running.is_set())

    try:
        fut_a = pool.submit(task_a)
        assert a_parked.wait(2)
        fut_b = pool.submit(task_b)
        assert b_running.wait(2)
        fut_c = pool.submit(task_c)
        # A 停靠 + B 运行已占满 base+parked=2，C 必须等待
        time.sleep(0.2)
        assert not fut_c.done()
        fut_a.result(timeout=2)
        fut_b.result(timeout=2)
        fut_c.result(timeout=2)
    finally:
        pool.shutdown()


def test_relief_threads_retire_after_backoff():
    pool = LeaseThreadPool(max_workers=1, thread_name_prefix="t-retire")
    parked = threading.Event()

    def parking_task():
        from office_agent.thread_lease import current_lease
        parked.set()
        current_lease().park(0.3)

    try:
        pool.submit(parking_task)
        assert parked.wait(2)
        futs = [pool.submit(lambda: None) for _ in range(3)]
        for fut in futs:
            fut.result(timeout=2)
        deadline = time.monotonic() + 2
        while pool._live > 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pool._live == 1
    finally:
        pool.shutdown()


def test_cancel_event_wakes_park_immediately():
    pool = LeaseThreadPool(max_workers=1, thread_name_prefix="t-cancel")
    cancel_event = threading.Event()
    outcome = {}

    def parking_task():
        from office_agent.thread_lease import current_lease
        started = time.monotonic()
        outcome["interrupted"] = current_lease().park(30, cancel_event)
        outcome["elapsed"] = time.monotonic() - started

    try:
        fut = pool.submit(parking_task)
        time.sleep(0.1)
        cancel_event.set()
        fut.result(timeout=2)
        assert outcome["interrupted"] is True
        assert outcome["elapsed"] < 1
    finally:
        pool.shutdown()


def test_future_semantics_match_thread_pool():
    pool = LeaseThreadPool(max_workers=1, thread_name_prefix="t-future")
    blocker = threading.Event()
    try:
        def blocking():
            blocker.wait(2)
            return None

        fut_block = pool.submit(blocking)
        # 排队中的 Future 可以取消
        fut_queued = pool.submit(lambda: "never")
        assert fut_queued.cancel() is True
        assert fut_queued.cancelled() is True

        callbacks = []
        fut_block.add_done_callback(lambda f: callbacks.append(f.done()))
        blocker.set()
        assert fut_block.result(timeout=2) is None
        assert callbacks == [True]

        def boom():
            raise ValueError("x")

        with pytest.raises(ValueError):
            pool.submit(boom).result(timeout=2)
    finally:
        pool.shutdown()


def test_shutdown_rejects_new_submissions():
    pool = LeaseThreadPool(max_workers=1, thread_name_prefix="t-shut")
    pool.submit(lambda: None).result(timeout=2)
    pool.shutdown()
    with pytest.raises(RuntimeError):
        pool.submit(lambda: None)


class _OneFlakyModel:
    """首个模型首次调用返回可重试错误，第二次成功。"""

    def __init__(self):
        self.calls = 0

    def get_routing(self, _task_type):
        return ["model"]

    def get_model(self, _model_id):
        return SimpleNamespace(enabled=True, api_key="key")

    def get_client(self, _model_id):
        return object()

    def action(self, _client):
        from office_agent.models.model_schemas import ModelResponse
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(success=False, error="timeout")
        return ModelResponse(success=True, content="ok", model_used="model")


def test_failover_backoff_releases_worker_slot_for_queued_task():
    from office_agent.model_gateway.failover import FailoverManager
    from office_agent.models.model_schemas import AITaskType
    from office_agent.task_queue.worker import LocalWorker

    quick_done = threading.Event()

    def flaky_task(progress=None, **_kwargs):
        manager = _OneFlakyModel()
        failover = FailoverManager(manager, max_retries=3, retry_delay=4.0)
        response = failover.execute_with_failover(
            AITaskType.SIMPLE_TEXT, manager.action,
            cancel_event=getattr(progress, "cancel_event", None),
        )
        assert response.success, response.error
        meta = (response.raw_response or {}).get("_office_agent", {})
        # 重试语义不变：第二次尝试成功、仍命中首选模型
        assert meta["attempts"] == 2
        assert meta["attempted_models"] == ["model"]
        return {"status": "success"}

    def quick_task(progress=None, **_kwargs):
        quick_done.set()
        return {"status": "success"}

    worker = LocalWorker()
    worker._session_factory = None
    worker._update_status = lambda *_args, **_kwargs: None
    worker.register("test.flaky", flaky_task)
    worker.register("test.quick", quick_task)
    try:
        normal_concurrency = worker.executors["normal"]._base
        task_ids = [
            worker.submit("test.flaky") for _ in range(normal_concurrency)
        ]
        time.sleep(0.3)  # 让所有 flaky 任务进入首次失败后的退避
        started = time.monotonic()
        worker.submit("test.quick")
        assert quick_done.wait(2), (
            "全部 normal 线程退避时，排队任务未能及时获得线程"
        )
        assert time.monotonic() - started < 2
        for task_id in task_ids:
            worker.revoke(task_id)
    finally:
        worker.shutdown()


def test_failover_without_lease_keeps_legacy_wait():
    """非执行器线程（无租约）保持原有 cancel_event.wait 行为。"""
    from office_agent.model_gateway.failover import FailoverManager
    from office_agent.models.model_schemas import AITaskType

    manager = _OneFlakyModel()
    failover = FailoverManager(manager, max_retries=3, retry_delay=0.05)
    response = failover.execute_with_failover(
        AITaskType.SIMPLE_TEXT, manager.action,
    )
    assert response.success
    meta = (response.raw_response or {}).get("_office_agent", {})
    assert meta["attempts"] == 2


def test_cooldown_and_backup_semantics_unchanged_with_lease():
    """租约停靠后冷却统计与备用模型切换顺序不变。"""
    from office_agent.model_gateway.failover import FailoverManager
    from office_agent.models.model_schemas import AITaskType, ModelResponse

    attempts = []

    class Manager:
        def get_routing(self, _task_type):
            return ["primary", "backup"]

        def get_model(self, _model_id):
            return SimpleNamespace(enabled=True, api_key="key")

        def get_client(self, _model_id):
            return object()

    def action(_client):
        return ModelResponse(success=False, error="timeout")

    pool = LeaseThreadPool(max_workers=1, thread_name_prefix="t-sem")

    def run():
        # 冷却阈值为 5 分钟内失败 >=3 次：max_retries=3 使 primary 恰好进入冷却
        failover = FailoverManager(Manager(), max_retries=3, retry_delay=0.05)
        response = failover.execute_with_failover(AITaskType.SIMPLE_TEXT, action)
        meta = (response.raw_response or {}).get("_office_agent", {})
        attempts.append(meta)
        # primary 失败 max_retries 次 + backup 失败 max_retries 次
        assert failover._is_in_cooldown("primary") is True
        assert not response.success

    try:
        pool.submit(run).result(timeout=5)
    finally:
        pool.shutdown()
    assert attempts[0]["attempts"] == 6
    assert attempts[0]["attempted_models"] == ["primary", "backup"]
    assert attempts[0]["fallback_used"] is True
