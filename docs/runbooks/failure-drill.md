# 故障演练 Runbook（Phase 6 门④）

> 状态：定稿 + 脚本化演练（scripts/drill_failures.py）。
> 目标：逐依赖击杀验证降级矩阵——任何单点故障下服务行为可预期（非崩溃、可观测、可恢复）。

## 降级矩阵

| 故障 | readyz 判定 | API 行为 | 恢复 |
|---|---|---|---|
| Qdrant 损坏/丢失 | qdrant: down → **503 degraded** | 检索/摄取 500 信封（内部错误不泄露）；探针可探测 | 按 backup-restore.md 场景 B 重索引 |
| SQLite 损坏/丢失 | database: down → **503 degraded** | 全部数据面 500 信封；探针可探测 | 按 backup-restore.md 场景 A 恢复 |
| LLM 端点不可达 | llm: down（**非关键**，不阻断就绪） | Chat RAG/透传 502 信封；检索不受影响 | 重启推理服务 |
| worker 停 | 无探测项（异步面） | 摄取任务保持排队（filesystem broker 持久），worker 恢复后消费 | 重启 worker |

## 演练方法

`uv run python scripts/drill_failures.py`（故障注入为测试隔离环境：
Qdrant/SQLite/LLM 三场景经 monkeypatch 注入真实故障语义，断言 readyz
判定与 API 信封；worker 场景由 enqueue 幂等测试与 validate_worker 覆盖）。

## 运营要求

- 探针告警：readyz critical down 已接边沿触发告警（main.py）
- 恢复后：跑 smoke.py 确认全链路，再切流量
