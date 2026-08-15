# structlog 接入计划（observability.md §2 落地）

> 状态：设计定稿（Phase 4 进入标准项）；实现随 Phase 5 首批。
> 关联：core/observability/logging.py（基建已就绪：JSON 行 + 字段白名单 + 脱敏 + emit_alert）

## 现状差距

- 基建：structlog 配置（contextvars merge + 白名单过滤 + JSONRenderer）与 `get_logger`/`emit_alert` 已实现并有测试
- 差距：**业务链路零埋点**——core/ 内仅 ratelimit.py 使用 stdlib logging；
  apps/api 路由与 SearchService/ChatClient/IngestPipeline 无结构化日志；
  trace_id 只在 HTTP 响应头，未进入日志上下文

## 设计决策

### D1 · trace_id 上下文传递（contextvars）

middleware 生成 trace_id 后立即绑定，请求结束清除：

```python
# apps/api/main.py trace_middleware（现实现基础上）
request.state.trace_id = uuid.uuid4().hex[:16]
structlog.contextvars.clear_contextvars()
structlog.contextvars.bind_contextvars(trace_id=request.state.trace_id)
try:
    response = await call_next(request)
finally:
    structlog.contextvars.clear_contextvars()
```

- 检索/重排/生成同步调用链随 contextvars 自动携带 trace_id，无需函数签名穿透
- Celery worker 无 HTTP middleware：ingest 任务在任务入口绑 `trace_id=job_id`（任务链四阶段同一条 trace）
- 白名单已含 `trace_id`，无需改过滤

### D2 · 埋点清单（最小集，逐点论证）

| 埋点 | 字段 | 论证 |
|---|---|---|
| search 路由成功 | kb_id, duration_ms, hits | 检索质量/延迟观测核心；query 与内容**不落日志**（白名单兜底 + 显式不 bind） |
| search 拒答 | kb_id, detail="refused" | 拒答率指标（与 refusal_threshold 联动） |
| chat LLM 调用 | duration_ms, model | LLM 延迟观测；消息体不落日志 |
| ingest 阶段完成 | kb_id, doc_id, stage, duration_ms | 摄取吞吐与阶段耗时（配合 ingest_jobs 表） |
| 限流触发 | actor, limit | 攻击/滥用检测信号（现状 ratelimit.py stdlib → 迁移 structlog） |

**不埋点**：请求体、查询文本、chunk 内容、文档内容、明文 Key、Cookie 值——
白名单机制（`_ALLOWED_KEYS`）是兜底不是许可，埋点代码不 bind 任何敏感字段。

### D3 · 与 metrics 的分工

- metrics（MetricsCollector）：聚合数值（计数器/分位数），用于 Prometheus 面板
- structlog：离散事件（含 trace_id 关联），用于排障与告警线索（emit_alert 通道）
- 二者共用事件源但不互相替代：日志不聚合，指标不关联单次请求

### D4 · 告警通道

`emit_alert` 维持独立事件形态（alert=true），Phase 6 由外部 collector 按
metric 字段路由到 Alertmanager；边沿触发策略已在 readyz/DLQ 落地，后续告警
一律沿用"状态转换才发"原则。

## 实施步骤

1. trace_middleware 绑定/清除 contextvars（+ 测试断言日志行含 trace_id）
2. SearchService/ChatClient/IngestPipeline 按 D2 清单埋点（TDD：先断言事件形态）
3. ratelimit.py stdlib → structlog 迁移
4. 冒烟/E2E 验证日志行 JSON 可解析且无白名单外字段
