# ADR-005 · 限流实现（本地内存令牌桶，无 Redis 环境）

- 状态：已批准
- 日期：2026-08-15
- 背景：架构 §12 限流方案为 Redis 令牌桶（按 Key + 按 KB）；实机无 Redis（ADR-002 同因）

## 决策

| 场景 | 实现 |
|---|---|
| Phase 3-5（本地） | **RateLimiter Protocol + 内存令牌桶**（core/security/ratelimit.py）：进程内字典 + 单调时钟，无外部依赖；跨进程一致性不保证（单进程部署可接受） |
| Phase 6（生产 Linux） | Redis 令牌桶（切换实现，接口不变） |

## 接口

```python
class RateLimiter(Protocol):
    def allow(self, key: str, capacity: int, refill_per_s: float) -> bool: ...
```

## 分层配额（审计 F-04）

| 类别 | 默认配额（可配置） |
|---|---|
| 查询类（search/chat） | 60 次/分钟/Key |
| 摄取提交类 | 10 次/分钟/Key（防队列膨胀） |
| 嵌入/重排代理类 | 30 次/分钟/Key |
| 认证错误尝试 | 10 次/分钟/IP（防爆破） |
| LLM 并发 | 信号量 ≤4（超出 429） |

## 约束

- 超限返回 **429** + 统一信封（错误码目录补 `rate_limited`）
- 限流失败（异常）**放行**（fail-open，限流是保护而非阻断；生产 Redis 亦然）——记录告警日志
- 测试：确定性时钟注入，令牌桶容量/补充速率手算验证
