# 摄取状态机契约（Phase 1 定稿）

> 状态：已批准 v1.1（quality.md Phase 1 进入标准⑤）· 2026-08-14
> v1.1 变更：① FAILED 增加恢复转移（重新入队时 FAILED → 失败发生阶段，重跑该阶段及其后续）
> ② 同状态转移为幂等 no-op（阶段方法在恢复后重入时可能已处于目标阶段）
> 对应架构 §5.1；实现者必须逐字遵循本契约，任何修改走 ADR。

## 1. 状态与转移

```
uploaded → parsed → chunked → embedded → indexed → ready
                                     ↘ failed（任意阶段可转移，携带 error）
```

| 状态 | 含义 | 进入条件 |
|---|---|---|
| uploaded | 原始文件已接收（落盘/对象存储） | 上传接口返回 job 时 |
| parsed | 解析完成（文本提取） | 解析器成功返回 ParsedDocument |
| chunked | 分块完成 | chunk 列表非空且写入注册表 |
| embedded | 嵌入完成 | 全部 chunk 向量就绪（内存/批内） |
| indexed | 向量已写入 Qdrant | upsert 成功（wait=True） |
| ready | 可检索 | indexed 且对账通过 |
| failed | 失败终态 | 任一阶段异常，error 记录原因 |

## 2. 硬性规则（实现必须遵守）

1. **合法转移白名单**：状态只能按上图前向推进；非法转移（如 chunked→uploaded）必须被拒绝（raise / 告警），禁止静默接受
2. **阶段幂等**：每阶段执行前检查当前状态——已完成阶段不重算（断点续传基础）；重试只重跑失败阶段及其后续
3. **failed 恢复**：failed → 重新入队从失败阶段重跑（非从头）
4. **替换语义**：重摄入同一文档时，chunk 与向量以 delete+insert 事务替换（架构 ARC-003），旧数据不残留
5. **对账**：indexed 状态写入后核对 Qdrant 点数 == 注册表 chunk 数，不一致转 failed
6. **持久化**：每次状态转移落库（ingest_jobs 表），进程崩溃后可恢复；状态机字段：
   `stage`（当前阶段）、`attempt`（重试次数）、`error`、`updated_at`

## 3. 数据模型变更（documents/ingest_jobs）

- `documents.status` 现值域扩展：`uploaded|parsed|chunked|embedded|indexed|ready|failed`（MVP 的 ready/failed 为子集，向后兼容）
- 新增 `documents.pipeline_version`（parser+chunker+embedder 三元组版本戳，审计 ARC-003）
- 新增 `ingest_jobs` 表：job_id、doc_id、kb_id、stage、attempt（≤5 后进 DLQ）、error、trace_id、timestamps
- 重试上限 **5 次**，超限进 DLQ（Web 管理端隔离区人工处置，审计 ARC-010）

## 4. 阶段任务边界（Celery）

| 任务 | 输入 | 输出 | 可重试 |
|---|---|---|---|
| parse_task | 文件路径 | ParsedDocument 文本 | 是 |
| chunk_task | 文本 | chunks（注册表已写） | 是 |
| embed_task | chunk 列表 | 向量（批量 128，架构 §5.6） | 是 |
| index_task | 向量 | Qdrant upsert + 对账 | 是 |

任务链式编排（Celery chain），单文档隔离；失败只影响本任务链，不阻塞队列。

## 5. 测试要求（表驱动，审计 F4）

- 全部合法转移逐一测试；非法转移矩阵（每对非法 (from,to) 拒绝）
- 断点续传：各阶段中断后重试不重算已完成阶段
- 幂等：同 (kb_id, content_hash) 重复提交只产生一次摄取
- 替换语义：重摄入后 Qdrant 与注册表无旧数据残留
