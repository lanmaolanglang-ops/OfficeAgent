"""
速率限制器 - 令牌桶和滑动窗口算法
"""
import time
import threading
from typing import Optional
from dataclasses import dataclass
from collections import deque


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: float
    retry_after: float = 0.0


class TokenBucket:
    """令牌桶算法。

    时间源使用 ``time.monotonic``：墙钟被 NTP/用户回拨时不会导致
    令牌凭空增加或 retry_after 变负（P2-8）。
    ``rate <= 0`` 表示 deny-all（永远拒绝，不抛 ZeroDivisionError，P2-6）。
    """

    def __init__(self, rate: int, per_seconds: int = 60, burst: Optional[int] = None,
                 clock=time.monotonic):
        if per_seconds <= 0:
            raise ValueError("per_seconds 必须为正数")
        self.rate = rate
        self.per_seconds = per_seconds
        self.capacity = burst or max(rate, 1)
        self._tokens = float(self.capacity) if rate > 0 else 0.0
        self._clock = clock
        self._last_refill = clock()
        self._lock = threading.Lock()

    @property
    def _refill_rate(self) -> float:
        """tokens / second；rate<=0 时为 0（deny-all）。"""
        if self.rate <= 0:
            return 0.0
        return self.rate / self.per_seconds

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        tokens_to_add = elapsed * self._refill_rate
        self._tokens = min(self.capacity, self._tokens + tokens_to_add)
        self._last_refill = now

    def consume(self, tokens: int = 1) -> RateLimitResult:
        with self._lock:
            self._refill()
            now = self._clock()
            if self.rate <= 0:
                # deny-all：reset_at 表示「配置修复前不会恢复」
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    reset_at=now + self.per_seconds,
                    retry_after=float(self.per_seconds),
                )
            if self._tokens >= tokens:
                self._tokens -= tokens
                # reset_at：距离下一次「至少 1 个令牌」可用的时间；
                # 桶已满时为 0（立刻可再取）。
                deficit = max(0.0, 1.0 - self._tokens)
                next_token_in = deficit / self._refill_rate if self._refill_rate else 0.0
                return RateLimitResult(
                    allowed=True,
                    remaining=int(self._tokens),
                    reset_at=now + next_token_in,
                    retry_after=0.0,
                )
            needed = tokens - self._tokens
            retry_after = needed / self._refill_rate
            return RateLimitResult(
                allowed=False,
                remaining=0,
                reset_at=now + retry_after,
                retry_after=retry_after,
            )


class SlidingWindowLimiter:
    """滑动窗口限流器"""

    # 最多跟踪的独立身份（防伪造 X-API-Key / 海量 IP 撑爆内存）
    MAX_IDENTITIES = 10000

    def __init__(self, max_requests: int, window_seconds: int = 60,
                 clock=time.monotonic):
        if window_seconds <= 0:
            raise ValueError("window_seconds 必须为正数")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._requests: dict[str, deque] = {}
        self._lock = threading.Lock()

    def is_allowed(self, identifier: str) -> RateLimitResult:
        with self._lock:
            now = self._clock()
            window_start = now - self.window_seconds
            if identifier not in self._requests:
                # 身份表有界：优先淘汰已过期窗口，其次淘汰最旧的活跃身份
                if len(self._requests) >= self.MAX_IDENTITIES:
                    stale = [k for k, w in self._requests.items()
                             if not w or w[-1] < window_start]
                    for k in stale:
                        del self._requests[k]
                    if len(self._requests) >= self.MAX_IDENTITIES:
                        oldest = sorted(self._requests.items(),
                                        key=lambda kv: kv[1][0] if kv[1] else 0)
                        # 上限低于 10 时 len//10 会退化成 0（一个都不淘汰，
                        # 身份表仍会越界增长）；至少淘汰最旧的 1 个身份。
                        evict_count = max(1, len(self._requests) // 10)
                        for k, _ in oldest[:evict_count]:
                            del self._requests[k]
                self._requests[identifier] = deque()
            window = self._requests[identifier]
            while window and window[0] < window_start:
                window.popleft()
            if len(window) < self.max_requests:
                window.append(now)
                return RateLimitResult(
                    allowed=True,
                    remaining=self.max_requests - len(window),
                    reset_at=window[0] + self.window_seconds if window else now + self.window_seconds,
                )
            else:
                reset_at = window[0] + self.window_seconds
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    reset_at=reset_at,
                    retry_after=max(0, reset_at - now),
                )

    def reset(self, identifier: Optional[str] = None) -> None:
        with self._lock:
            if identifier:
                self._requests.pop(identifier, None)
            else:
                self._requests.clear()

    def cleanup(self) -> int:
        """清理过期窗口"""
        with self._lock:
            now = self._clock()
            window_start = now - self.window_seconds
            count = 0
            for identifier in list(self._requests.keys()):
                window = self._requests[identifier]
                while window and window[0] < window_start:
                    window.popleft()
                if not window:
                    del self._requests[identifier]
                    count += 1
            return count


class RateLimiterManager:
    """速率限制管理器"""

    def __init__(self):
        self._limits: dict[str, SlidingWindowLimiter] = {}
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def add_limit(self, name: str, max_requests: int, window_seconds: int = 60) -> None:
        with self._lock:
            self._limits[name] = SlidingWindowLimiter(max_requests, window_seconds)

    def add_bucket(self, name: str, rate: int, per_seconds: int = 60, burst: Optional[int] = None) -> None:
        with self._lock:
            self._buckets[name] = TokenBucket(rate, per_seconds, burst)

    def check(self, limit_name: str, identifier: str) -> RateLimitResult:
        limiter = self._limits.get(limit_name)
        if limiter is None:
            return RateLimitResult(allowed=True, remaining=999, reset_at=time.monotonic() + 60)
        return limiter.is_allowed(identifier)

    def consume(self, bucket_name: str, tokens: int = 1) -> RateLimitResult:
        bucket = self._buckets.get(bucket_name)
        if bucket is None:
            return RateLimitResult(allowed=True, remaining=999, reset_at=time.monotonic() + 60)
        return bucket.consume(tokens)


# 全局实例（double-checked locking：多线程首次调用只创建一个）
_rate_limiter: Optional[RateLimiterManager] = None
_rate_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiterManager:
    global _rate_limiter
    if _rate_limiter is None:
        with _rate_limiter_lock:
            if _rate_limiter is None:
                manager = RateLimiterManager()
                manager.add_limit("api", 100, 60)
                manager.add_limit("model", 50, 60)
                manager.add_limit("upload", 50, 3600)
                # 注：曾注册过一个 "model_calls" 令牌桶，但仓库内从未有任何
                # consume("model_calls") 调用——误导性的死配置，已移除。
                _rate_limiter = manager
    return _rate_limiter
