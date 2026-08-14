"""检索服务：文档摄取（解析→分块→嵌入→入库）与查询（嵌入→检索→回查正文）。"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.ingest.chunker import chunk_text
from core.ingest.parsers import parse_file
from core.retrieval.hybrid import HybridRetriever
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
    content: str


class SearchService:
    def __init__(
        self,
        store: VectorStore,
        registry: Registry,
        embedder,
        chunk_size: int = 512,
        overlap: int = 64,
        max_pdf_pages: int | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._max_pdf_pages = max_pdf_pages
        self._hybrid_cache: dict[str, HybridRetriever] = {}  # KB 级混合检索缓存

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
        parsed = parse_file(path, max_pages=self._max_pdf_pages)
        content_hash = hashlib.sha256(parsed.text.encode("utf-8")).hexdigest()

        existing = self._registry.find_document_by_hash(kb_id, content_hash)
        if existing is not None:
            return existing

        title = title or parsed.title
        chunks = chunk_text(parsed.text, self._chunk_size, self._overlap)
        if not chunks:
            raise EmptyDocumentError(f"文档 {title} 解析后无文本内容")

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
            self._registry.mark_document_failed(doc.id, str(exc))
            raise
        result = self._registry.get_document(kb_id, doc.id)
        if result is None:
            raise RuntimeError(f"文档 {doc.id} 摄取后未找到（数据不一致）")
        return result

    def _get_hybrid(self, kb_id: str) -> HybridRetriever:
        if kb_id not in self._hybrid_cache:
            self._hybrid_cache[kb_id] = HybridRetriever(
                self._store, self._registry, kb_id, rrf_k=60
            )
        return self._hybrid_cache[kb_id]

    def _invalidate_hybrid(self, kb_id: str) -> None:
        self._hybrid_cache.pop(kb_id, None)

    def search(self, kb_id: str, query: str, top_k: int = 5) -> list[SearchResult]:
        """混合检索（dense + BM25 → RRF，架构 §6）+ 正文回查；结果按融合分数降序。"""
        vector = self._embedder.embed([query])[0]
        hits = self._get_hybrid(kb_id).search(query, vector, top_k=top_k)
        if not hits:
            return []
        chunk_ids = [h.chunk_id for h in hits]
        contents = self._registry.get_chunk_contents(chunk_ids)
        docs_by_chunk = self._registry.get_chunk_doc_map(chunk_ids)
        docs = self._registry.get_documents_by_ids(list(set(docs_by_chunk.values())))
        results = []
        for h in hits:
            doc_id = docs_by_chunk.get(h.chunk_id, "")
            doc = docs.get(doc_id)
            results.append(
                SearchResult(
                    chunk_id=h.chunk_id,
                    doc_id=doc_id,
                    doc_title=doc.title if doc else "",
                    score=h.score,
                    dense_score=h.dense_score if h.dense_score is not None else 0.0,
                    content=contents.get(h.chunk_id, ""),
                )
            )
        return results

    def delete_document(self, kb_id: str, doc_id: str) -> None:
        """删除文档：先删向量再删注册表（保证查询不命中已删文档的 chunk）。"""
        self._store.delete_by_document(kb_id, doc_id)
        self._registry.delete_document(kb_id, doc_id)
        self._invalidate_hybrid(kb_id)
