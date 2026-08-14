"""混合检索（架构 §6）：dense（向量库）+ sparse（BM25，ADR-004）→ RRF 融合。

设计约束（ADR-004）：
- BM25 索引为 KB 级内存结构（本地路径）；生产切 Qdrant 原生 full-text
- 融合用 core/retrieval/rrf.py 纯函数（不依赖 Qdrant 内置 RRF）
- dense 与 sparse 各取 top-50，融合后取 top-N；过滤（kb_id）在 dense 侧前置
"""
from dataclasses import dataclass

from core.retrieval.bm25 import BM25Retriever
from core.retrieval.rrf import reciprocal_rank_fusion
from core.storage.registry import Registry
from core.storage.vector import VectorStore


@dataclass(frozen=True)
class HybridHit:
    chunk_id: str
    score: float  # RRF 融合分数（排序用）
    dense_score: float | None  # dense 余弦相似度（拒答判定用，审计 ARC-014）


class HybridRetriever:
    def __init__(
        self,
        store: VectorStore,
        registry: Registry,
        kb_id: str,
        rrf_k: int = 60,
        dense_limit: int = 50,
        sparse_limit: int = 50,
    ) -> None:
        self._store = store
        self._kb_id = kb_id
        self._rrf_k = rrf_k
        self._dense_limit = dense_limit
        self._sparse_limit = sparse_limit
        chunks = registry.list_chunks(kb_id)
        self._chunk_ids = [cid for cid, _ in chunks]
        self._bm25 = BM25Retriever([content for _, content in chunks])

    def search(self, query: str, query_vector: list[float], top_k: int = 5) -> list[HybridHit]:
        dense = self._store.search(query_vector, self._kb_id, limit=self._dense_limit)
        dense_scores = {p.id: p.score for p in dense}
        sparse = self._bm25.search(query, top_k=self._sparse_limit)
        fusion = reciprocal_rank_fusion(
            [
                [p.id for p in dense],
                [self._chunk_ids[h.id] for h in sparse],
            ],
            k=self._rrf_k,
        )
        ranked = sorted(fusion.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [
            HybridHit(chunk_id=cid, score=score, dense_score=dense_scores.get(cid))
            for cid, score in ranked
        ]
