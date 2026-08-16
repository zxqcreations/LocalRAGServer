# Qdrant dense+sparse 混合检索迁移计划（Phase 2 进入标准）

> 状态：**已被 ADR-004 取代**（2026-08-14 决策：Qdrant local 模式无 full-text
> 索引，改用纯 Python BM25 独立稀疏通道 + RRF 融合，无需此迁移）。
> 本文档存档保留设计讨论。

## 1. 现状

- collection `chunks`：named vectors 仅 `dense`（1024 维 cosine）
- bge-m3 dense 本机可用（Spike 实测 141 条/s）
- **sparse 来源决策（2026-08-14 实测）**：本机 sentence-transformers 无 bge-m3 sparse 输出 API
  （`sparse_embeddings` 参数不受支持）→ **采用 Qdrant 内置 BM25 稀疏索引**（对 payload 文本字段建
  BM25 index，SparseIndexParams 已实测可用）；bge-m3 学习稀疏标注为生产 TEI 路径的增强选项
- 千万级 chunk 目标规模（架构 §8.3）：全量重嵌入 ≈ 数十小时

## 2. 方案：新 collection + 双向量全量重建 + 原子切换（选定）

| 步骤 | 操作 | 验证点 |
|---|---|---|
| 1 | 新建 collection `chunks_v2`：`dense`（1024 cosine）+ payload 文本字段（`content` 或短正文片段，供 BM25 sparse index）| collection 创建成功、参数正确 |
| 2 | 双写窗口：摄取管线同时写入 v1/v2（新文档两边都写），存量文档入重索引队列 | 新文档两边可见 |
| 3 | 存量全量重建（后台，分批按 KB）：dense 重嵌入 + payload 文本字段写入 | 每批对账：v2 点数 == 注册表 chunk 数 |
| 4 | 评测集回归：v2 上跑 recall@10/MRR@10（混合检索实现完成后） | 基线门禁不下降 |
| 5 | **原子切换**：配置翻转 collection 名（`RAG_QDRANT_COLLECTION=chunks_v2`），检索/摄取读新名 | 切换零停机（配置重载或重启，秒级） |
| 6 | 保留 `chunks` 旧 collection ≥30 天供回滚；期间快照入库 | 回滚 = 配置翻转 |
| 7 | 30 天后删除旧 collection | 快照留存 |

## 3. 回滚预案

- 配置翻回 `chunks` 即回滚（双写窗口保证期间数据一致）
- 若双写窗口未启用，回滚时补跑切换期间增量重索引（按 updated_at 扫描 ingest_jobs）

## 4. 前置依赖（2026-08-14 全部实测通过）

1. ✅ Qdrant Query API prefetch/RRF：本机 qdrant-client 1.19 已确认可用
2. ✅ SparseVector/SparseIndexParams（BM25 稀疏索引）：已确认可用
3. ✅ bge-m3 dense GPU 可用（141 条/s）；sparse 走 Qdrant BM25（见 §1 决策）
4. ⏸️ TEI 部署方案定稿（见 tei-deployment-options.md——方案 A 已完整可行，TEI 降级为 Phase 6 增强项）

## 5. 决策记录

- 选择「新 collection + 双写 + 原子切换」而非原地加向量：原地 add sparse 向量需对全量点做 partial update（写入放大、无回滚点）；新 collection 提供干净回滚与渐进迁移
- 与架构 ARC-003「双 collection 切换」一致
