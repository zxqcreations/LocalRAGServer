"""限流（ADR-005：RateLimiter Protocol + 内存令牌桶；生产切 Redis 接口不变）。

fail-open 语义：限流是保护而非阻断，内部异常时放行并记录（ADR-005 约束）。
"""
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger("local_rag_server")


@runtime_checkable
class RateLimiter(Protocol):
    def allow(self, key: str, capacity: int, refill_per_s: float) -> bool: ...


class InMemoryTokenBucket:
    """单调时钟令牌桶；clock 可注入（确定性测试手算验证）。"""

    def __init__(self, clock=None) -> None:
        import time as _time

        self._clock = clock or _time.monotonic
        self._buckets: dict[tuple[str, int, float], tuple[float, float]] = {}

    def allow(self, key: str, capacity: int, refill_per_s: float) -> bool:
        now = self._clock()
        bucket_key = (key, capacity, refill_per_s)
        state = self._buckets.get(bucket_key)
        if state is None:
            tokens, last = float(capacity), now
        else:
            tokens, last = state
            tokens = min(float(capacity), tokens + (now - last) * refill_per_s)
        if tokens >= 1.0:
            self._buckets[bucket_key] = (tokens - 1.0, now)
            return True
        self._buckets[bucket_key] = (tokens, now)
        return False


class FailOpenLimiter:
    """包装任意 RateLimiter：异常时放行 + 告警日志（ADR-005）。"""

    def __init__(self, inner: RateLimiter) -> None:
        self._inner = inner

    def allow(self, key: str, capacity: int, refill_per_s: float) -> bool:
        try:
            return self._inner.allow(key, capacity, refill_per_s)
        except Exception:
            logger.exception("限流组件异常，fail-open 放行：key=%s", key)
            return True


def build_limiter() -> RateLimiter:
    return FailOpenLimiter(InMemoryTokenBucket())
