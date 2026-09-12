"""P2-11：调度器跳过轮必须推进 next_run（禁止结束后秒补跑）回归。

修复前：``_execute`` 在 ``task.running`` 时直接 ``return False``，发生在
推进 ``next_run`` 之前——长任务跨越自己的下一周期时，跳过轮不推进；
当前任务一结束，``next_run`` 仍是过去时间，调度器立即补跑一次
（skip → finish → immediate catch-up）。产品语义：running 时本轮直接
丢弃，下一次按下一正常周期执行。
"""
from datetime import datetime, timedelta, timezone

import pytest

import office_agent.task_queue as task_queue
from office_agent.task_queue import scheduler as scheduler_module
from office_agent.task_queue.scheduler import ScheduledTask


class _FakeWorker:
    def __init__(self):
        self.registered = {}
        self.submitted = []

    def register(self, name, func, *, replace=False):
        self.registered[name] = (func, replace)

    def submit(self, queue_name, priority=None, **_kwargs):
        self.submitted.append((queue_name, priority))


@pytest.fixture
def fake_worker(monkeypatch):
    worker = _FakeWorker()
    monkeypatch.setattr(task_queue, "init_worker", lambda: worker)
    return worker


def _make_task(interval=60):
    return ScheduledTask("job", lambda: None, interval)


class TestSkipRoundAdvancesNextRun:
    def test_running_task_is_skipped_and_next_run_advances(self, fake_worker):
        sched = scheduler_module.TaskScheduler()
        task = _make_task(interval=60)
        task.running = True
        task.next_run = datetime.now(timezone.utc) - timedelta(seconds=10)

        before = task.next_run
        result = sched._execute(task)

        assert result is False
        assert task.next_run > before, "跳过轮也必须推进 next_run"
        # 推进到下一合法周期（当前时刻 + 一个完整 interval，容许时钟误差）
        now = datetime.now(timezone.utc)
        assert task.next_run >= now + timedelta(seconds=59)
        # 跳过不算一次执行
        assert task.run_count == 0
        assert task.last_run is None

    def test_finished_task_does_not_catch_up_immediately(self, fake_worker):
        """当前任务结束后不秒补跑：完成时 next_run 必须仍在未来。"""
        sched = scheduler_module.TaskScheduler()
        task = _make_task(interval=60)
        task.running = True
        task.next_run = datetime.now(timezone.utc) - timedelta(seconds=10)

        sched._execute(task)  # 本轮被丢弃

        # 模拟当前任务在 ~30s 后结束：完成后 next_run 必须仍在未来，
        # 调度主循环不会再立即触发它。
        assert task.next_run > datetime.now(timezone.utc)

    def test_consecutive_skip_rounds_keep_advancing(self, fake_worker):
        sched = scheduler_module.TaskScheduler()
        task = _make_task(interval=60)
        task.running = True
        task.next_run = datetime.now(timezone.utc) - timedelta(seconds=10)

        first = sched._execute(task)
        second_skip = task.next_run
        second = sched._execute(task)

        assert first is False and second is False
        assert task.next_run >= second_skip, "连续跳过轮不能让 next_run 回退"
        assert task.next_run > datetime.now(timezone.utc)

    def test_naive_next_run_is_normalized_on_skip(self, fake_worker):
        """naive next_run（旧夹具兼容口径）在跳过分支同样可用。"""
        sched = scheduler_module.TaskScheduler()
        task = _make_task(interval=60)
        task.running = True
        task.next_run = datetime.now() - timedelta(seconds=10)  # naive

        assert sched._execute(task) is False
        assert task.next_run.tzinfo is not None
        assert task.next_run > datetime.now(timezone.utc)


class TestNormalPathUnchanged:
    def test_normal_execution_advances_next_run(self, fake_worker):
        sched = scheduler_module.TaskScheduler()
        task = _make_task(interval=60)

        result = sched._execute(task)

        assert result is True
        assert task.run_count == 1
        assert task.next_run > datetime.now(timezone.utc)
        queue_name, priority = fake_worker.submitted[0]
        assert queue_name == "scheduled.job"
        assert priority == "low"

    def test_scheduler_registers_per_fire_closure_with_replace(self, fake_worker):
        """每次触发注册携带本次上下文的新闭包：显式按次替换，非静默覆盖。"""
        sched = scheduler_module.TaskScheduler()
        task = _make_task(interval=60)
        sched._execute(task)
        _, replace = fake_worker.registered["scheduled.job"]
        assert replace is True
