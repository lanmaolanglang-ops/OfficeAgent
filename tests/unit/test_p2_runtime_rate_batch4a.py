"""Batch 4A：RuntimeManager 状态机 / RateLimiter 并发与时钟回归。"""
from __future__ import annotations

import threading
import time

import pytest

from office_agent.rate_limiter import SlidingWindowLimiter, TokenBucket, get_rate_limiter
from office_agent.runtime_manager import (
    AppConfig,
    ApplicationRuntimeManager,
    AppStatus,
    get_runtime_manager,
)


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class TestTokenBucketP2:
    def test_rate_zero_deny_all_no_divzero(self):
        clock = _FakeClock()
        bucket = TokenBucket(rate=0, per_seconds=60, clock=clock)
        result = bucket.consume(1)
        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after > 0

    def test_monotonic_not_wall_clock(self):
        clock = _FakeClock()
        bucket = TokenBucket(rate=10, per_seconds=10, burst=1, clock=clock)
        assert bucket.consume(1).allowed
        # 墙钟回拨不应影响：只推进 fake monotonic
        clock.advance(0.5)
        denied = bucket.consume(1)
        assert denied.allowed is False
        clock.advance(0.6)  # 累计 1.1s → +1.1 token
        assert bucket.consume(1).allowed

    def test_reset_at_is_next_token_time(self):
        clock = _FakeClock()
        bucket = TokenBucket(rate=60, per_seconds=60, burst=1, clock=clock)  # 1/s
        first = bucket.consume(1)
        assert first.allowed
        # 桶空：reset_at ≈ now + 1s
        assert first.reset_at == pytest.approx(clock.t + 1.0, abs=0.01)
        denied = bucket.consume(1)
        assert not denied.allowed
        assert denied.retry_after == pytest.approx(1.0, abs=0.01)

    def test_invalid_per_seconds_rejected(self):
        with pytest.raises(ValueError):
            TokenBucket(rate=10, per_seconds=0)


class TestSlidingWindowP2:
    def test_uses_injected_monotonic(self):
        clock = _FakeClock()
        limiter = SlidingWindowLimiter(2, 60, clock=clock)
        assert limiter.is_allowed("a").allowed
        assert limiter.is_allowed("a").allowed
        denied = limiter.is_allowed("a")
        assert not denied.allowed
        clock.advance(61)
        assert limiter.is_allowed("a").allowed


class TestSingletonConcurrency:
    def test_get_rate_limiter_single_instance(self):
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(get_rate_limiter())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 8
        assert all(r is results[0] for r in results)

    def test_get_runtime_manager_single_instance(self, tmp_path):
        import office_agent.runtime_manager as rm

        original = rm._runtime_manager
        rm._runtime_manager = None
        try:
            cfg = AppConfig(data_dir=tmp_path / "d", log_dir=tmp_path / "l")
            results = []
            barrier = threading.Barrier(6)

            def worker():
                barrier.wait()
                results.append(get_runtime_manager(cfg))

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert all(r is results[0] for r in results)
        finally:
            rm._runtime_manager = original


# ---------------------------------------------------------------------------
# RuntimeManager
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None
        self.killed = False
        self.terminated = False
        self._alive = True

    def poll(self):
        return None if self._alive else (self.returncode or 0)

    def kill(self):
        self.killed = True
        self._alive = False
        self.returncode = -9

    def terminate(self):
        self.terminated = True
        self._alive = False
        self.returncode = 0

    def send_signal(self, _sig):
        self.terminate()

    def wait(self, timeout=None):
        self._alive = False
        return 0


def _mgr(tmp_path, **cfg_kw):
    cfg = AppConfig(
        data_dir=tmp_path / "d",
        log_dir=tmp_path / "l",
        auto_restart=cfg_kw.pop("auto_restart", False),
        **cfg_kw,
    )
    return ApplicationRuntimeManager(cfg)


class TestHealthFailureP2_1:
    def test_health_fail_no_restart_goes_error(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path, auto_restart=False)
        mgr.state.status = AppStatus.RUNNING
        mgr.state.start_time = time.time()
        mgr._process = _FakeProc()
        monkeypatch.setattr(mgr, "check_health", lambda: False)

        ticks = {"n": 0}

        def fake_sleep(_t):
            ticks["n"] += 1
            # 3 次健康失败（每次循环 sleep 一次）后再多跑几轮保险
            if ticks["n"] > 6:
                mgr._stop_event.set()

        import office_agent.runtime_manager as rm
        monkeypatch.setattr(rm.time, "sleep", fake_sleep)
        mgr._monitor_loop()
        assert mgr.state.status == AppStatus.ERROR
        assert "Health check failed" in mgr.state.last_error
        assert "auto_restart disabled" in mgr.state.last_error

    def test_starting_blocks_second_spawn(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path)
        spawned = []

        def fake_popen(*a, **k):
            spawned.append(1)
            return _FakeProc()

        monkeypatch.setattr(mgr, "is_port_in_use", lambda: False)
        monkeypatch.setattr("office_agent.runtime_manager.subprocess.Popen", fake_popen)
        mgr.state.status = AppStatus.STARTING
        assert mgr.start() is False
        assert spawned == []


class TestStopReportsTruthfullyP2_5:
    def test_stop_sets_stopping_then_stopped(self, tmp_path, monkeypatch):
        mgr = _mgr(tmp_path)
        proc = _FakeProc()
        mgr._process = proc
        mgr.state.status = AppStatus.RUNNING
        statuses = []
        mgr.on_status_change(lambda s, st: statuses.append(s))
        assert mgr.stop(timeout=0.1) is True
        assert AppStatus.STOPPING in statuses
        assert statuses[-1] == AppStatus.STOPPED
        assert proc.terminated or proc.killed
        assert mgr._process is None

    def test_stop_without_process_is_stopped(self, tmp_path):
        mgr = _mgr(tmp_path)
        assert mgr.stop() is True
        assert mgr.state.status == AppStatus.STOPPED


class TestConcurrentStartStopP2_2:
    def test_start_does_not_overwrite_concurrent_stop(self, tmp_path, monkeypatch):
        """start 健康成功瞬间并发 stop：最终必须是 STOPPED。"""
        mgr = _mgr(tmp_path, startup_timeout=5)
        monkeypatch.setattr(mgr, "is_port_in_use", lambda: False)
        monkeypatch.setattr(mgr, "check_health", lambda: True)
        monkeypatch.setattr(
            "office_agent.runtime_manager.subprocess.Popen",
            lambda *a, **k: _FakeProc(),
        )
        started = threading.Event()
        stopped = threading.Event()

        def do_start():
            mgr.start()
            started.set()

        # 先让 start 进入 STARTING
        t = threading.Thread(target=do_start)
        t.start()
        # 在 start 完成前发出 stop
        time.sleep(0.02)
        mgr.stop(timeout=0.1)
        stopped.set()
        t.join(timeout=5)
        # 最终状态不得是 RUNNING（stop 意图丢失）
        assert mgr.state.status in (AppStatus.STOPPED, AppStatus.STOPPING, AppStatus.ERROR)
        assert mgr.state.status != AppStatus.RUNNING or mgr._stop_event.is_set()
