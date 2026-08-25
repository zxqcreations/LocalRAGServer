"""检索服务：文档摄取（解析→分块→嵌入→入库）与查询（嵌入→检索→回查正文）。"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.ingest.chunker import Chunk, chunk_text
from core.ingest.parsers import parse_file
from core.retrieval.hybrid import HybridRetriever
from core.retrieval.parent import expand_parents
from core.retrieval.rerank import NoopReranker, Reranker
from core.retrieval.router import route
from core.storage.registry import Document, Registry
from core.storage.vector import VectorPoint, VectorStore


class EmptyDocumentError(ValueError):
    """文档解析后没有可索引的文本内容。"""


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    doc_id: str
    doc_title: str
    score: float  # 融合排序分（RRF）
    dense_score: float  # dense 语义相似度（拒答判定）
    content: str  # 命中块原文
    expanded_content: str  # parent 回填后的上下文（生成用，架构 §5/§6）


class SearchService:
    def __init__(
        self,
        store: VectorStore,
        registry: Registry,
        embedder,
        chunk_size: int = 512,
        overlap: int = 64,
        max_pdf_pages: int | None = None,
        reranker: Reranker | None = None,
        retrieval_top_k: int = 50,
        rerank_top_k: int = 8,
    ) -> None:
        self._store = store
        self._registry = registry
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._max_pdf_pages = max_pdf_pages
        self._hybrid_cache: dict[str, HybridRetriever] = {}  # KB 级混合检索缓存
        # KB 级 chunk 序列缓存（parent 回填用；SLO 压测：全量加载 ~430ms/查询，
        # 必须复用——摄取/删除时失效）
        self._chunks_cache: dict[str, tuple[list[str], dict[str, str], dict[str, str]]] = {}
        self._reranker = reranker or NoopReranker()
        self._retrieval_top_k = retrieval_top_k
        self._rerank_top_k = rerank_top_k

    def ensure_ready(self) -> None:
        self._store.ensure_collection(self._embedder.dim)

    @property
    def store(self) -> VectorStore:
        return self._store

    @property
    def embedder(self):
        return self._embedder

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    @property
    def overlap(self) -> int:
        return self._overlap

    def ingest_file(
        self, kb_id: str, path: str | Path, title: str | None = None, source: str | None = None
    ) -> Document:
        """解析并索引单个文件；同内容重复上传幂等返回已有文档。

        title/source 用于保留上传时的原始文件名（本地落盘可能是临时文件名）。
        """
        import logging
        _logger = logging.getLogger("local_rag_server")
        try:
            _logger.warning("Ingest start", {
                "kb_id": kb_id,
                "path": str(path),
                "title": title,
                "source": source,
            })
            parsed = parse_file(path, max_pages=self._max_pdf_pages)
        except Exception as exc:
            _logger.exception("ParseFile failed", {"path": str(path), "error": str(exc)})
            raise
        # 清理文本（null bytes / 非法 UTF-8 会导致 SQLite 写入失败）
        clean_text = parsed.text.encode("utf-8", errors="replace").decode("utf-8")
        content_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()

        existing = self._registry.find_document_by_hash(kb_id, content_hash)
        print(1.5, path, existing.error if existing else None)
        if existing is not None:
            return existing

        title = title or parsed.title
        chunks = chunk_text(clean_text, self._chunk_size, self._overlap)
        if not chunks:
            raise EmptyDocumentError(f"文档 {title} 解析后无文本内容")

        # 再次清理 chunk 文本（防止 chunker 产生含二进制内容的字符串）
        chunks = [
            Chunk(
                index=c.index,
                text=c.text.encode("utf-8", errors="replace").decode("utf-8"),
                start=c.start,
                end=c.end,
            )
            for c in chunks
        ]

        doc = self._registry.create_document(
            kb_id=kb_id,
            title=title,
            source=source or str(Path(path)),
            content_hash=content_hash,
        )
        try:
            vectors = self._embedder.embed([c.text for c in chunks])
            chunk_ids = self._registry.set_chunks(doc.id, kb_id, chunks)
            points = [
                VectorPoint(
                    id=chunk_id,
                    vector=vector,
                    payload={"kb_id": kb_id, "doc_id": doc.id, "chunk_index": chunk.index},
                )
                for chunk_id, vector, chunk in zip(chunk_ids, vectors, chunks, strict=True)
            ]
            self._store.upsert(points)
            self._invalidate_hybrid(kb_id)
        except Exception as exc:
            self._registry.mark_document_failed(doc.id, f"{type(exc).__name__}: {exc}")
            _logger.error("Ingest failed for doc %s", doc.id, exc_info=True)
            raise
        result = self._registry.get_document(kb_id, doc.id)
        if result is None:
            raise RuntimeError(f"文档 {doc.id} 摄取后未找到（数据不一致）")
        return result

    def debug_search(self, kb_id: str, query: str, top_k: int = 5) -> dict:
        """调试台三阶段数据（审计 F17：粗排→融合→重排同一中间结构，前端只渲染）。"""
        vector = self._embedder.embed([query])[0]
        hybrid = self._get_hybrid(kb_id)
        # 阶段 1：粗排（dense + sparse 各自候选）
        dense = self._store.search(vector, kb_id, limit=self._retrieval_top_k)
        sparse = hybrid._bm25.search(query, top_k=self._retrieval_top_k)  # noqa: SLF001 同包调试接口
        chunk_ids = [h.chunk_id for h in hybrid.search(query, vector, top_k=top_k)]
        contents = self._registry.get_chunk_contents(chunk_ids)
        # 阶段 2：融合（RRF 前 top_k）；阶段 3：重排后最终序（此处返回融合序+重排分）
        return {
            "dense": [
                {"chunk_id": p.id, "score": p.score} for p in dense
            ],
            "sparse": [
                {"chunk_id": hybrid._chunk_ids[h.id], "score": h.score}  # noqa: SLF001
                for h in sparse
            ],
            "fused": [
                {"chunk_id": cid, "content": contents.get(cid, "")[:200]}
                for cid in chunk_ids
            ],
            "final": [
                {"chunk_id": cid, "content": contents.get(cid, "")[:200]}
                for cid in chunk_ids
            ],
        }

    def _get_hybrid(self, kb_id: str) -> HybridRetriever:
        if kb_id not in self._hybrid_cache:
            params = route(self._kb_type(kb_id))
            self._hybrid_cache[kb_id] = HybridRetriever(
                self._store,
                self._registry,
                kb_id,
                rrf_k=params.rrf_k,
                dense_limit=params.dense_limit,
                sparse_limit=params.sparse_limit,
            )
        return self._hybrid_cache[kb_id]

    def _kb_type(self, kb_id: str) -> str:
        kb = self._registry.get_kb(kb_id)
        return kb.kb_type if kb is not None else "document"

    def _invalidate_hybrid(self, kb_id: str) -> None:
        self._hybrid_cache.pop(kb_id, None)
        self._chunks_cache.pop(kb_id, None)

    def _chunk_sequences(self, kb_id: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
        """KB 级 chunk 序列缓存（all_ids, ordered, doc_map）。"""
        if kb_id not in self._chunks_cache:
            all_ids = [cid for cid, _ in self._registry.list_chunks(kb_id)]
            ordered = dict(self._registry.list_chunks(kb_id))
            doc_map = self._registry.get_chunk_doc_map(all_ids)
            self._chunks_cache[kb_id] = (all_ids, ordered, doc_map)
        return self._chunks_cache[kb_id]

    def search(self, kb_id: str, query: str, top_k: int = 5) -> list[SearchResult]:
        """混合检索（dense + BM25 → RRF，架构 §6）→ 重排 → top-N 截断 → 正文回查。

        score 语义：reranker 存在时 = 重排分数（排序用）；否则 = RRF 融合分数。
        dense_score 恒为 dense 语义相似度（拒答判定用，审计 ARC-014）。
        """
        vector = self._embedder.embed([query])[0]
        hits = self._get_hybrid(kb_id).search(query, vector, top_k=self._retrieval_top_k)
        if not hits:
            return []
        chunk_ids = [h.chunk_id for h in hits]
        contents = self._registry.get_chunk_contents(chunk_ids)
        # 重排（降级路径为 NoopReranker，保持融合序）
        ordered_contents = [contents.get(cid, "") for cid in chunk_ids]
        rerank_scores = self._reranker.rerank(query, ordered_contents)
        reranked = sorted(
            zip(hits, rerank_scores, strict=True),
            key=lambda pair: pair[1],
            reverse=True,
        )[: self._rerank_top_k]
        top_k_hits = [h for h, _ in reranked]
        top_k_scores = [s for _, s in reranked]
        docs_by_chunk = self._registry.get_chunk_doc_map([h.chunk_id for h in top_k_hits])
        docs = self._registry.get_documents_by_ids(list(set(docs_by_chunk.values())))
        # parent 回填（架构 §5/§6）：命中子块扩展相邻上下文（KB 级序列缓存）
        expanded = expand_parents(
            self._registry,
            kb_id,
            [h.chunk_id for h in top_k_hits],
            target_size=2048,
            chunks_cache=self._chunk_sequences(kb_id),
        )
        results = []
        for h, rerank_score in zip(top_k_hits, top_k_scores, strict=True):
            doc_id = docs_by_chunk.get(h.chunk_id, "")
            doc = docs.get(doc_id)
            results.append(
                SearchResult(
                    chunk_id=h.chunk_id,
                    doc_id=doc_id,
                    doc_title=doc.title if doc else "",
                    score=rerank_score,
                    dense_score=h.dense_score if h.dense_score is not None else 0.0,
                    content=contents.get(h.chunk_id, ""),
                    expanded_content=expanded.get(h.chunk_id, contents.get(h.chunk_id, "")),
                )
            )
        return results[:top_k]

    def delete_document(self, kb_id: str, doc_id: str) -> None:
        """删除文档：先删向量再删注册表（保证查询不命中已删文档的 chunk）。"""
        self._store.delete_by_document(kb_id, doc_id)
        self._registry.delete_document(kb_id, doc_id)
        self._invalidate_hybrid(kb_id)
