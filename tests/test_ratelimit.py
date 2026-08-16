"""令牌桶限流测试（ADR-005：确定性时钟手算容量/补充速率/fail-open + Redis 后端）。"""
from core.config import Settings
from core.security.ratelimit import (
    AsyncLimiterBridge,
    FailOpenLimiter,
    InMemoryTokenBucket,
    RedisTokenBucket,
    build_limiter,
)


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


def test_fail_open_event_carries_exception(capsys):
    # 审查 H1：fail-open 事件必须携带异常诊断（ExceptionRenderer 产物）
    import json

    class BoomLimiter:
        def allow(self, key, capacity, refill_per_s):
            raise RuntimeError("限流组件故障")

    limiter = FailOpenLimiter(BoomLimiter())
    assert limiter.allow("k", 1, 1.0) is True
    out = capsys.readouterr().out
    events = [
        json.loads(line)
        for line in out.splitlines()
        if line.startswith("{") and "rate_limiter_fail_open" in line
    ]
    assert events, "未捕获到 rate_limiter_fail_open 事件"
    assert "限流组件故障" in events[-1].get("exception", "")


# ---------- Redis 令牌桶（ADR-005 Phase 6：stub 客户端验证协议面；Lua 语义
# 由 scripts/drill_redis_ratelimit.py 对真机验证） ----------


class FakeRedisScript:
    """stub：register_script 返回自身，__call__ 记录 (keys, args) 并回放响应。"""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[tuple[list, list]] = []

    def register_script(self, lua):
        return self

    def __call__(self, keys=None, args=None):
        self.calls.append((list(keys or []), list(args or [])))
        return self._responses.pop(0)


class BoomRedisScript:
    def register_script(self, lua):
        def boom(keys=None, args=None):
            raise ConnectionError("redis down")

        return boom


def test_redis_bucket_allow_and_key_format():
    clock = FakeClock(start=1000.0)
    bucket = RedisTokenBucket(FakeRedisScript([1, 0]), clock=clock)
    assert bucket.allow("ip:1.2.3.4", 30, 0.5) is True  # Lua 返回 1
    assert bucket.allow("ip:1.2.3.4", 30, 0.5) is False  # Lua 返回 0
    assert bucket._script.calls == [
        (["ratelimit:ip:1.2.3.4:30:0.5"], [30, 0.5, 1000.0, 2 * 60 + 60]),
        (["ratelimit:ip:1.2.3.4:30:0.5"], [30, 0.5, 1000.0, 180]),
    ]


def test_redis_bucket_ttl_and_refill_zero():
    bucket = RedisTokenBucket(FakeRedisScript([1]))
    assert bucket._ttl(10, 0.1) == 260  # 2×满桶时间 + 60s（具体值防公式回归）
    assert bucket._ttl(30, 0.5) == 180
    assert bucket._ttl(10, 0.0) == 86400  # 无补充 → 键保留一天


def test_redis_bucket_fail_open():
    # Redis 故障经 FailOpenLimiter 放行（ADR-005：保护而非阻断）
    limiter = FailOpenLimiter(RedisTokenBucket(BoomRedisScript(), clock=FakeClock()))
    assert limiter.allow("k", 1, 1.0) is True


def test_fail_open_degrades_to_fallback():
    # 安全审查 M：运行期 Redis 故障降级进程内桶（保护持续，而非放行全开）
    class BoomLimiter:
        def allow(self, key, capacity, refill_per_s):
            raise RuntimeError("限流组件故障")

    limiter = FailOpenLimiter(BoomLimiter(), fallback=InMemoryTokenBucket())
    assert limiter.allow("k", 1, 0.0) is True  # 降级桶满容量放行
    assert limiter.allow("k", 1, 0.0) is False  # 降级桶耗尽拒绝（保护生效）


def test_fail_open_edge_triggered_logging(capsys):
    # 代码审查 M：持续故障不刷屏（仅首次记完整异常）
    import json

    class BoomLimiter:
        def allow(self, key, capacity, refill_per_s):
            raise RuntimeError("限流组件故障")

    limiter = FailOpenLimiter(BoomLimiter())
    for _ in range(3):
        assert limiter.allow("k", 1, 1.0) is True
    out = capsys.readouterr().out
    events = [
        json.loads(line)
        for line in out.splitlines()
        if line.startswith("{") and "rate_limiter_fail_open" in line
    ]
    assert len(events) == 1  # 边沿触发：三次失败只记一次


def test_clock_step_backward_clamped():
    # 安全审查 L：时钟回拨不得产生负令牌债
    clock = FakeClock()
    bucket = InMemoryTokenBucket(clock=clock)
    assert bucket.allow("k", capacity=1, refill_per_s=1.0) is True
    assert bucket.allow("k", capacity=1, refill_per_s=1.0) is False  # 空桶
    clock.advance(-10)  # NTP 回拨
    assert bucket.allow("k", capacity=1, refill_per_s=1.0) is False  # 负债被钳制为 0
    clock.advance(2.0)  # 正常补充 2 个 → 封顶 1
    assert bucket.allow("k", capacity=1, refill_per_s=1.0) is True


def test_async_bridge_threaded_runs_off_loop():
    import asyncio

    bridge = AsyncLimiterBridge(
        RedisTokenBucket(FakeRedisScript([1, 0]), clock=FakeClock(start=1000.0)),
        threaded=True,
    )
    assert asyncio.run(bridge.allow("k", 1, 1.0)) is True
    assert asyncio.run(bridge.allow("k", 1, 1.0)) is False  # 第二响应回放 0


def test_build_limiter_defaults_to_memory(tmp_path):
    settings = Settings(data_dir=tmp_path)  # redis_url 空
    limiter = build_limiter(settings)
    assert isinstance(limiter, AsyncLimiterBridge)
    assert isinstance(limiter._inner, FailOpenLimiter)
    assert isinstance(limiter._inner._inner, InMemoryTokenBucket)
    assert limiter._threaded is False  # 纯 CPU 直调，无线程跳板
    assert isinstance(build_limiter(), AsyncLimiterBridge)  # 无参调用兼容


def test_build_limiter_falls_back_when_redis_unreachable(tmp_path, capsys):
    # 端口 1 连接即拒 → ping 失败 → 回退内存（部署层 fail-open 兜底）；
    # 告警日志中的 URL 必须脱敏（安全审查 H）
    import json

    settings = Settings(
        data_dir=tmp_path, redis_url="redis://:secret-pass@127.0.0.1:1/0"
    )
    limiter = build_limiter(settings)
    assert isinstance(limiter, AsyncLimiterBridge)
    assert isinstance(limiter._inner._inner, InMemoryTokenBucket)
    out = capsys.readouterr().out
    fallback_events = [
        json.loads(line)
        for line in out.splitlines()
        if line.startswith("{") and "rate_limiter_fallback" in line
    ]
    assert fallback_events
    detail = fallback_events[-1]["detail"]
    assert "secret-pass" not in detail  # 凭据不入日志
    assert "127.0.0.1:1" in detail  # 仅 host:port
