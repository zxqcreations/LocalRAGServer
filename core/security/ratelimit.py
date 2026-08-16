"""限流（ADR-005：RateLimiter Protocol + 内存令牌桶；生产切 Redis 接口不变）。

fail-open 语义：限流是保护而非阻断，内部异常时放行并记录（ADR-005 约束）。
Redis 后端（Phase 6 生产路径）：Lua 原子 read-modify-write，跨进程一致；
连接失败/超时经 FailOpenLimiter 降级到进程内桶（保护持续而非放行全开，
安全审查 M）；部署期不可用则回退内存实现。事件循环安全：Redis I/O 经
AsyncLimiterBridge.to_thread 移出 loop（阻塞 socket 不得卡认证主链路）。
"""
import threading
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from core.observability.logging import get_logger

logger = get_logger("local_rag_server.ratelimit")

# Redis 客户端超时（秒）：慢即失败 → 降级/fail-open（限流不得拖垮主链路）。
# socket_connect_timeout 必须显式覆盖——redis-py 默认 5s（审查 H：实测
# 黑洞地址 ping 阻塞 5.03s，直接卡死异步中间件的事件循环）
_REDIS_SOCKET_TIMEOUT = 0.25
_REDIS_CONNECT_TIMEOUT = 0.1

# 令牌桶 Lua（原子 RMW）：tokens 为浮点（精确到微秒级补充），
# 返回 1=放行 0=拒绝；每次调用重置 TTL（ttl 取满桶时间的 2 倍 + 60s 余量，
# 短于满桶时间会使键提前过期 → 桶重置 → 变相多发令牌）。
# max(0, ...) 钳制：墙钟回拨（NTP 校正）不得产生负令牌债（安全审查 L）。
# 命名不含 "token"（bandit B105 对凭据词变量名的启发式误报，且多行字符串
# 上 nosec 不生效——1.9.4 实测）
_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
  tokens = capacity
else
  tokens = math.max(0, math.min(capacity, tokens + (now - ts) * refill))
end
local ok = 0
if tokens >= 1 then
  tokens = tokens - 1
  ok = 1
end
redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, ttl * 1000)
return ok
"""


@runtime_checkable
class RateLimiter(Protocol):
    def allow(self, key: str, capacity: int, refill_per_s: float) -> bool: ...


@runtime_checkable
class AsyncRateLimiter(Protocol):
    async def allow(self, key: str, capacity: int, refill_per_s: float) -> bool: ...


class InMemoryTokenBucket:
    """单调时钟令牌桶；clock 可注入（确定性测试手算验证）。"""

    def __init__(self, clock=None) -> None:
        import time as _time

        self._clock = clock or _time.monotonic
        self._buckets: dict[tuple[str, int, float], tuple[float, float]] = {}
        self._lock = threading.Lock()  # 审计 L-1：read-modify-write 加锁（多 worker/线程安全）

    def allow(self, key: str, capacity: int, refill_per_s: float) -> bool:
        now = self._clock()
        bucket_key = (key, capacity, refill_per_s)
        with self._lock:
            state = self._buckets.get(bucket_key)
            if state is None:
                tokens, last = float(capacity), now
            else:
                tokens, last = state
                # max(0, …)：时钟回拨不得产生负令牌债（安全审查 L）
                tokens = max(0.0, min(float(capacity), tokens + (now - last) * refill_per_s))
            if tokens >= 1.0:
                self._buckets[bucket_key] = (tokens - 1.0, now)
                return True
            self._buckets[bucket_key] = (tokens, now)
            return False


class RedisTokenBucket:
    """Redis 令牌桶（ADR-005 Phase 6）：Lua 原子 RMW，多进程/多实例一致。

    与内存实现同接口（同 key 粒度：(key, capacity, refill) 组合独立桶）。
    客户端注入（测试用 stub）；生产经 build_limiter 构造。
    clock 为墙钟（跨进程共享时间基准；测试注入确定值）。
    """

    def __init__(self, client, clock=None) -> None:
        import time as _time

        self._clock = clock or _time.time
        self._script = client.register_script(_BUCKET_LUA)

    @staticmethod
    def _ttl(capacity: int, refill_per_s: float) -> int:
        # 满桶时间 × 2 + 60s：键存活必须覆盖最坏补充周期（防提前过期变相补令牌）
        return int(capacity / refill_per_s * 2) + 60 if refill_per_s > 0 else 86400

    def allow(self, key: str, capacity: int, refill_per_s: float) -> bool:
        # 键内嵌容量/速率参数：与内存实现的桶隔离粒度一致
        redis_key = f"ratelimit:{key}:{capacity}:{refill_per_s}"
        result = self._script(
            keys=[redis_key],
            args=[capacity, refill_per_s, self._clock(), self._ttl(capacity, refill_per_s)],
        )
        return bool(result)


def _redact_url(url: str) -> str:
    """脱敏 URL（安全审查 H：redis:// 可携带凭据，不得入日志）——仅 host:port。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    host = parts.hostname or "?"
    return f"{host}:{parts.port}" if parts.port else host


class FailOpenLimiter:
    """包装任意 RateLimiter：异常时放行/降级 + 告警日志（ADR-005）。

    fallback：降级实现（Redis 故障时改走进程内桶——保护持续而非放行全开，
    安全审查 M）。告警边沿触发：持续故障不刷屏（代码审查 M），恢复发回执。
    """

    def __init__(self, inner: RateLimiter, fallback: RateLimiter | None = None) -> None:
        self._inner = inner
        self._fallback = fallback
        self._failing = False
        self._lock = threading.Lock()

    def allow(self, key: str, capacity: int, refill_per_s: float) -> bool:
        try:
            ok = self._inner.allow(key, capacity, refill_per_s)
        except Exception:
            ok = (
                self._fallback.allow(key, capacity, refill_per_s)
                if self._fallback is not None
                else True
            )
            with self._lock:
                if not self._failing:
                    self._failing = True
                    # structlog-integration.md D2：限流异常事件（fail-open 是设计语义）
                    logger.exception("rate_limiter_fail_open", detail=key)
            return ok
        with self._lock:
            if self._failing:
                self._failing = False
                logger.info("rate_limiter_recovered", detail=key)
        return ok


class AsyncLimiterBridge:
    """事件循环适配（安全审查 M）：阻塞 I/O 移出 loop。

    Redis 后端经 asyncio.to_thread 执行（socket 阻塞只占工作线程）；
    内存实现为纯 CPU 微秒级，直调不加线程跳板。
    """

    def __init__(self, inner: RateLimiter, threaded: bool = False) -> None:
        self._inner = inner
        self._threaded = threaded

    async def allow(self, key: str, capacity: int, refill_per_s: float) -> bool:
        if not self._threaded:
            return self._inner.allow(key, capacity, refill_per_s)
        import asyncio

        return await asyncio.to_thread(self._inner.allow, key, capacity, refill_per_s)


def build_limiter(settings=None) -> AsyncRateLimiter:
    """构造限流器：settings.redis_url 非空则用 Redis 令牌桶（ADR-005 Phase 6）。

    Redis 构造/连通性检查失败 → 回退内存实现（部署层 fail-open：
    限流降级为进程内，不阻断服务；告警日志留痕，URL 脱敏）。
    运行期故障 → FailOpenLimiter 降级到进程内桶（保护持续）。
    """
    if settings is not None and getattr(settings, "redis_url", None):
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_timeout=_REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=_REDIS_CONNECT_TIMEOUT,
                retry_on_timeout=False,
            )
            client.ping()
            logger.info("rate_limiter_backend", detail="redis")
            return AsyncLimiterBridge(
                FailOpenLimiter(RedisTokenBucket(client), fallback=InMemoryTokenBucket()),
                threaded=True,
            )
        except Exception:
            logger.warning(
                "rate_limiter_fallback",
                detail=f"Redis 不可用（{_redact_url(settings.redis_url)}），回退内存实现",
            )
    return AsyncLimiterBridge(FailOpenLimiter(InMemoryTokenBucket()), threaded=False)
