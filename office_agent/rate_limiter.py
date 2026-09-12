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
    """令牌桶算法"""

    def __init__(self, rate: int, per_seconds: int = 60, burst: Optional[int] = None):
        self.rate = rate
        self.per_seconds = per_seconds
        self.capacity = burst or rate
        self._tokens = float(self.capacity)
        self._last_refill = time.time()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.time()
        elapsed = now - self._last_refill
        tokens_to_add = elapsed * (self.rate / self.per_seconds)
        self._tokens = min(self.capacity, self._tokens + tokens_to_add)
        self._last_refill = now

    def consume(self, tokens: int = 1) -> RateLimitResult:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return RateLimitResult(
                    allowed=True,
                    remaining=int(self._tokens),
                    reset_at=time.time() + self.per_seconds,
                )
            else:
                needed = tokens - self._tokens
                retry_after = needed / (self.rate / self.per_seconds)
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    reset_at=time.time() + retry_after,
                    retry_after=retry_after,
                )


class SlidingWindowLimiter:
    """滑动窗口限流器"""

    # 最多跟踪的独立身份（防伪造 X-API-Key / 海量 IP 撑爆内存）
    MAX_IDENTITIES = 10000

    def __init__(self, max_requests: int, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, deque] = {}
        self._lock = threading.Lock()

    def is_allowed(self, identifier: str) -> RateLimitResult:
        with self._lock:
            now = time.time()
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
                        for k, _ in oldest[: len(self._requests) // 10]:
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
            now = time.time()
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
            return RateLimitResult(allowed=True, remaining=999, reset_at=time.time() + 60)
        return limiter.is_allowed(identifier)

    def consume(self, bucket_name: str, tokens: int = 1) -> RateLimitResult:
        bucket = self._buckets.get(bucket_name)
        if bucket is None:
            return RateLimitResult(allowed=True, remaining=999, reset_at=time.time() + 60)
        return bucket.consume(tokens)


# 全局实例
_rate_limiter: Optional[RateLimiterManager] = None


def get_rate_limiter() -> RateLimiterManager:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiterManager()
        _rate_limiter.add_limit("api", 100, 60)
        _rate_limiter.add_limit("model", 50, 60)
        _rate_limiter.add_limit("upload", 50, 3600)
        # 注：曾注册过一个 "model_calls" 令牌桶，但仓库内从未有任何
        # consume("model_calls") 调用——误导性的死配置，已移除。
    return _rate_limiter
