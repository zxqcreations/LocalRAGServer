# 评测集与检索回归（Day 1 种子，审计 F9/ARC-015）

## 目录

```
eval/
├── datasets/qa.jsonl        # 评测集 v1：50 条，kb_type 分层 + 锚定 + is_hard
├── fixtures/docs/           # 种子语料（document/code/web 三类）
│   ├── document/…           # 技术文档 ×2
│   ├── code/…               # 代码 ×2
│   └── web/…                # 网页风格文章 ×1
├── dataset.py               # 加载与校验（自洽性：锚定存在性/长度/去重）
└── run_retrieval.py         # 离线检索回归（recall@k / MRR@k，不依赖 LLM）
```

## 运行

```bash
uv run python -m eval.run_retrieval          # 回归报告（stub 基线 recall@10≈0.88）
uv run pytest tests/test_eval.py             # 评测集校验 + 回归下限断言
```

## 设计原则

1. **锚定文本子串而非 chunk id**：分块内容确定但 chunk id 随机（uuid），跨运行回归必须用文本锚点
2. **锚点长度 ≤40 字符**：必须落在单个 chunk 内（分块 150 起），防止跨块切割导致无法命中
3. **版本化**：任何评测结果必须记录评测集版本（当前 v1 = 本目录 commit 状态）；跨版本结果不可比
4. **污染隔离**：线上真实查询不得写入本评测集；Phase 5 起另建生产标注集并定期抽查
5. **judge 独立选型**（Phase 5 引入 RAGAS 时）：忠实度维度用与生成模型不同的 judge，防自评偏置

## 基线策略

| 阶段 | 嵌入 | 基线 |
|---|---|---|
| Phase 0-1 | stub（哈希袋，开发级） | recall@10 ≈ 0.88（下限 0.85 防回归） |
| Phase 2+ | bge-m3 真实嵌入 | **以评测集 v1 重定权威基线**，目标 recall@10 ≥ 0.95；接入 CI 作为质量门（指标不下降才可合并） |

## 评测集条目格式（JSONL）

```json
{"id": "qa-001", "kb_type": "document", "question": "……", "reference_answer": "……",
 "anchor_doc": "quantum_computing", "anchor_text": "量子比特（qubit）是",
 "is_hard": false, "annotated_by": "seed", "annotated_at": "2026-08-14"}
```
