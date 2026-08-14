# ADR-004 · 稀疏检索实现（纯 Python BM25，本地路径）

- 状态：已批准
- 日期：2026-08-14
- 实测依据：本机 Qdrant 本地模式（1.19）无 `create_full_text_index` 能力、纯文本查询报
  "向量集合不存在"（文本被当作向量名解析）——**本地模式不支持 full-text/BM25 索引**

## 决策

| 场景 | 实现 |
|---|---|
| Phase 2-5（本地） | **纯 Python BM25**（core/retrieval/bm25.py）：倒排索引 + IDF + BM25 打分（k1=1.5, b=0.75）；tokenizer：拉丁按词、CJK 按字符 |
| Phase 6（生产 Linux） | Qdrant server 原生 full-text index（`TextIndexParams`），切换走评测回归门禁 |

## 约束

1. BM25 索引为内存结构（本地开发路径）；千万级 chunk 规模不适用——生产必须切 Qdrant 原生
2. 混合融合统一走 core/retrieval/rrf.py（手算测试固化），不依赖 Qdrant 内置 RRF（本地模式亦无 prefetch 全文能力）
3. 稀疏侧召回 top-50 与 dense top-50 融合后取 top-N（架构 §6），过滤优先于融合

## 后果

- 正面：零新依赖、完全离线、行为可手算验证；评测集回归可直接度量 BM25 贡献
- 负面：内存索引规模受限（本地开发可接受）；与生产实现差异需 Phase 6 切换回归
