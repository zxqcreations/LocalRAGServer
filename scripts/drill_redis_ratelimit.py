"""Redis 限流演练（ADR-005 Phase 6）：对真实 Redis 验证 Lua 令牌桶语义。

用法：
  RAG_REDIS_URL=redis://127.0.0.1:6379/0 uv run python scripts/drill_redis_ratelimit.py

验证项（任一失败退出非零）：
1. 容量耗尽拒绝：capacity=2/refill=0 → 两次放行后持续拒绝
2. 时间补充：refill=10/s 睡 0.3s → 恢复放行
3. 键隔离：不同 (key, capacity, refill) 组合独立桶
4. 键 TTL：桶键带 PEXPIRE（无补充桶默认保留一天）
"""
import sys
import time

from core.config import get_settings
from core.observability.logging import configure_logging
from core.security.ratelimit import RedisTokenBucket

configure_logging(stream=sys.stderr)


def _check(name: str, condition: bool) -> None:
    if not condition:
        print(f"[FAIL] {name}")
        sys.exit(1)
    print(f"[OK]   {name}")


def main() -> None:
    settings = get_settings()
    if not settings.redis_url:
        print("未配置 RAG_REDIS_URL（用法见模块 docstring）")
        sys.exit(1)
    import redis

    client = redis.Redis.from_url(settings.redis_url, socket_timeout=1.0)
    client.ping()  # 连接失败直接抛（演练不吞异常，区别于生产 fail-open）
    bucket = RedisTokenBucket(client)
    keys: list[str] = []  # 失败路径同样清理（代码审查 L：不留脏键）

    def _cleanup() -> None:
        if keys:
            client.delete(*keys)

    try:
        # 1) 容量耗尽拒绝
        key = f"drill:{time.time_ns()}"
        bucket.allow(key, 2, 0.0)
        bucket.allow(key, 2, 0.0)
        _check("容量耗尽拒绝（capacity=2/refill=0）", bucket.allow(key, 2, 0.0) is False)
        keys.append(f"ratelimit:{key}:2:0.0")

        # 2) 时间补充语义：refill=0 的桶时间流逝仍拒绝；refill=10/s 的桶睡后恢复
        time.sleep(0.3)
        _check("refill=0 桶时间流逝仍拒绝", bucket.allow(key, 2, 0.0) is False)
        key2 = f"drill:{time.time_ns()}"
        _check("补充桶首次放行", bucket.allow(key2, 1, 10.0) is True)
        time.sleep(0.2)
        _check("补充后再次放行（10/s × 0.2s）", bucket.allow(key2, 1, 10.0) is True)
        keys.append(f"ratelimit:{key2}:1:10.0")

        # 3) 键隔离
        key3 = f"drill:{time.time_ns()}"
        _check("键隔离（独立桶各自满容量）", bucket.allow(key3, 1, 0.0) is True)
        _check("键隔离（互不影响）", bucket.allow(key, 2, 0.0) is False)
        keys.append(f"ratelimit:{key3}:1:0.0")

        # 4) 键 TTL 存在
        ttl_ms = client.pttl(f"ratelimit:{key3}:1:0.0")
        _check(f"桶键带 TTL（pttl={ttl_ms}ms > 0）", ttl_ms > 0)
    finally:
        _cleanup()
    print("Redis 限流演练通过")


if __name__ == "__main__":
    main()
