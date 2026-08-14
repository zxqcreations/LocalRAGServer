# 可观测性与告警设计（Phase 4 进入标准 · ARC-006/F-05 定稿）

> 状态：已批准 · 2026-08-15

## 1. 指标清单（9 类，架构 §11 定稿）

| # | 指标 | 实现 |
|---|---|---|
| 1 | 检索延迟 P50/P95/P99 | 中间件计时，per-endpoint 标签 |
| 2 | 重排耗时 | reranker 调用计时 |
| 3 | 嵌入吞吐（chunk/s） | 摄取管线阶段计时 |
| 4 | 队列深度/积压 | celery 代理目录计数 + ingest_jobs 未终态计数 |
| 5 | Qdrant 容量（points/集合数） | store.count 定期采样 |
| 6 | GPU 显存/利用率 | nvidia-smi 采样（无 GPU 环境自动跳过） |
| 7 | 拒答率 | chat 路由拒答分支计数 / 总 RAG 请求 |
| 8 | API 错误率（4xx/5xx） | 中间件状态码计数 |
| 9 | 审计事件量 | audit_logs 表计数 |

实现：无外部监控依赖（本机无 Prometheus）——**内置 /admin/api/metrics 端点**
（admin 会话鉴权）+ Web 看板消费；Prometheus 导出器留 Phase 6 Linux 迁移
（指标收集层用统一 MetricsCollector 接口，切换零业务改动）。

## 2. 结构化日志与 trace_id

- **structlog**：JSON 行输出；字段白名单（time/level/event/trace_id/actor/kb_id/duration）
- **trace_id**：请求入口生成 → 注入 request.state → 贯穿 检索/重排/生成/摄取任务
  （ingest_jobs 已预留 trace_id 字段，审计 ARC-010）
- **脱敏**（审计 F-19）：请求体不落日志；查询文本默认不记录；API Key/Authorization 永不打码外泄

## 3. 告警规则集（ARC-006 定稿，Phase 4 落地）

| 规则 | 阈值 | 通道 |
|---|---|---|
| 检索 P95 劣化 | 相对基线 >2× | 日志 WARN + metrics 标记 |
| GPU 显存 | >90% | 日志 WARN（10 分钟窗口去重） |
| 磁盘 | >80% | 日志 WARN |
| 队列积压 | 未终态 job >1000 持续 10 分钟 | 日志 WARN |
| API 错误率 | >1%（5 分钟窗口） | 日志 WARN |
| 拒答率漂移 | 相对基线 ±50% | 日志 WARN |
| 备份失败 | 任意失败 | 日志 ERROR（Phase 6 备份任务接入后生效） |
| 评测回归 | 基线门禁失败 | CI 阻断（已有） |

通道说明：本机无 Alertmanager/邮件——**告警落地为结构化日志 ERROR/WARN 事件
（alert=true 字段）+ Web 看板告警面板**；Phase 6 Linux 迁移时接入 Alertmanager
（告警规则表即迁移清单）。

## 4. SLO 初稿（ARC-006）

| SLO | 目标 |
|---|---|
| 检索端到端 | P95 < 500ms（满数据量 Phase 6 压测验收） |
| 生成首 token | < 2s（llama.cpp 实测基线校准） |
| 可用性 | ≥ 99.5%（错误预算按季度评审） |
| 摄取完成率 | 24h 内 ≥ 99% |
