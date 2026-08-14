"""检索服务：文档摄取（解析→分块→嵌入→入库）与查询（嵌入→检索→回查正文）。"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.ingest.chunker import chunk_text
from core.ingest.parsers import parse_file
from core.storage.registry import Document, Registry
from core.storage.vector import VectorPoint, VectorStore


class EmptyDocumentError(ValueError):
    """文档解析后没有可索引的文本内容。"""


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    doc_id: str
    doc_title: str
    score: float
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

    def ensure_ready(self) -> None:
        self._store.ensure_collection(self._embedder.dim)

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
        except Exception as exc:
            self._registry.mark_document_failed(doc.id, str(exc))
            raise
        result = self._registry.get_document(kb_id, doc.id)
        if result is None:
            raise RuntimeError(f"文档 {doc.id} 摄取后未找到（数据不一致）")
        return result

    def search(self, kb_id: str, query: str, top_k: int = 5) -> list[SearchResult]:
        """dense 检索 + 正文回查；结果按分数降序。"""
        vector = self._embedder.embed([query])[0]
        scored = self._store.search(vector, kb_id, limit=top_k)
        if not scored:
            return []
        contents = self._registry.get_chunk_contents([s.id for s in scored])
        docs = self._registry.get_documents_by_ids(
            list({s.payload.get("doc_id", "") for s in scored})
        )
        results = []
        for s in scored:
            doc_id = s.payload.get("doc_id", "")
            doc = docs.get(doc_id)
            results.append(
                SearchResult(
                    chunk_id=s.id,
                    doc_id=doc_id,
                    doc_title=doc.title if doc else "",
                    score=s.score,
                    content=contents.get(s.id, ""),
                )
            )
        return results

    def delete_document(self, kb_id: str, doc_id: str) -> None:
        """删除文档：先删向量再删注册表（保证查询不命中已删文档的 chunk）。"""
        self._store.delete_by_document(kb_id, doc_id)
        self._registry.delete_document(kb_id, doc_id)
