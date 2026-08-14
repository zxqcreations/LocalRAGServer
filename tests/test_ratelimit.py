"""令牌桶限流测试（ADR-005：确定性时钟手算容量/补充速率/fail-open）。"""
from core.security.ratelimit import FailOpenLimiter, InMemoryTokenBucket


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_capacity_exhausted_then_refilled():
    clock = FakeClock()
    bucket = InMemoryTokenBucket(clock=clock)
    assert bucket.allow("k", capacity=2, refill_per_s=0.0) is True  # 消费 1
    assert bucket.allow("k", capacity=2, refill_per_s=0.0) is True  # 消费 2
    assert bucket.allow("k", capacity=2, refill_per_s=0.0) is False  # 空桶拒绝
    clock.advance(10)
    # refill=0：时间流逝不补充
    assert bucket.allow("k", capacity=2, refill_per_s=0.0) is False


def test_refill_rate_hand_computed():
    clock = FakeClock()
    bucket = InMemoryTokenBucket(clock=clock)
    assert bucket.allow("k", capacity=1, refill_per_s=0.5) is True  # 满桶消费
    assert bucket.allow("k", capacity=1, refill_per_s=0.5) is False
    clock.advance(2.0)  # 2s × 0.5/s = 1 个令牌
    assert bucket.allow("k", capacity=1, refill_per_s=0.5) is True
    assert bucket.allow("k", capacity=1, refill_per_s=0.5) is False


def test_bucket_cap_never_exceeds_capacity():
    clock = FakeClock()
    bucket = InMemoryTokenBucket(clock=clock)
    bucket.allow("k", capacity=1, refill_per_s=100.0)
    clock.advance(1000)  # 理论补充 100000 令牌 → 封顶 1
    assert bucket.allow("k", capacity=1, refill_per_s=100.0) is True
    assert bucket.allow("k", capacity=1, refill_per_s=100.0) is False


def test_keys_are_isolated():
    bucket = InMemoryTokenBucket(clock=FakeClock())
    assert bucket.allow("a", capacity=1, refill_per_s=0.0) is True
    assert bucket.allow("b", capacity=1, refill_per_s=0.0) is True  # 独立桶


def test_fail_open_limiter_swallows_errors():
    class BoomLimiter:
        def allow(self, key, capacity, refill_per_s):
            raise RuntimeError("限流组件故障")

    limiter = FailOpenLimiter(BoomLimiter())
    assert limiter.allow("k", 1, 1.0) is True  # fail-open 放行
