"""检索服务端到端单测（stub 嵌入 + 内存向量库 + SQLite 注册表，零外部依赖）。"""
import pytest

from core.retrieval.embeddings import StubEmbedder
from core.retrieval.search import EmptyDocumentError, SearchService
from core.storage.registry import Registry
from core.storage.vector import InMemoryVectorStore


@pytest.fixture
def service(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'registry.db'}")
    svc = SearchService(
        store=InMemoryVectorStore(),
        registry=registry,
        embedder=StubEmbedder(dim=64),
    )
    svc.ensure_ready()
    return svc, registry


def _make_doc(tmp_path, name: str, content: str):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_ingest_and_search(service, tmp_path):
    svc, registry = service
    kb = registry.create_kb("技术库")
    doc = svc.ingest_file(
        kb.id, _make_doc(tmp_path, "quantum.md", "# 量子\n\n量子计算使用量子比特与叠加态。")
    )
    assert doc.status == "ready"
    assert doc.chunk_count >= 1

    results = svc.search(kb.id, "量子比特", top_k=3)
    assert results
    assert results[0].content
    assert results[0].doc_title == "quantum"
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_is_kb_isolated(service, tmp_path):
    svc, registry = service
    kb1 = registry.create_kb("库一")
    kb2 = registry.create_kb("库二")
    doc1 = svc.ingest_file(kb1.id, _make_doc(tmp_path, "a.md", "苹果是一种水果。"))
    doc2 = svc.ingest_file(kb2.id, _make_doc(tmp_path, "b.md", "量子纠缠是物理现象。"))
    results = svc.search(kb2.id, "苹果", top_k=5)
    assert results  # kb2 有自己的文档，低分也会返回
    assert all(r.doc_id != doc1.id for r in results)  # 但不含 kb1 的文档
    assert any(r.doc_id == doc2.id for r in results)


def test_ingest_same_content_is_idempotent(service, tmp_path):
    svc, registry = service
    kb = registry.create_kb("库")
    p = _make_doc(tmp_path, "d.md", "同样内容的文档。")
    d1 = svc.ingest_file(kb.id, p)
    d2 = svc.ingest_file(kb.id, p)
    assert d1.id == d2.id
    assert registry.count_documents(kb.id) == 1


def test_ingest_empty_document_raises(service, tmp_path):
    svc, registry = service
    kb = registry.create_kb("库")
    with pytest.raises(EmptyDocumentError):
        svc.ingest_file(kb.id, _make_doc(tmp_path, "empty.md", "   "))
    assert registry.count_documents(kb.id) == 0


def test_ingest_embedding_failure_marks_document_failed(service, tmp_path):
    svc, registry = service
    kb = registry.create_kb("库")

    class BoomEmbedder:
        dim = 64

        def embed(self, texts):
            raise RuntimeError("模型不可用")

    broken = SearchService(
        store=InMemoryVectorStore(), registry=registry, embedder=BoomEmbedder()
    )
    broken.ensure_ready()
    with pytest.raises(RuntimeError):
        broken.ingest_file(kb.id, _make_doc(tmp_path, "d.md", "正常内容。"))

    docs = registry.list_documents(kb.id)
    assert len(docs) == 1
    assert docs[0].status == "failed"
    assert "模型不可用" in docs[0].error


def test_delete_document_removes_vectors_and_rows(service, tmp_path):
    svc, registry = service
    kb = registry.create_kb("库")
    doc = svc.ingest_file(kb.id, _make_doc(tmp_path, "d.md", "要被删除的内容。"))
    assert svc.search(kb.id, "删除", top_k=3)
    svc.delete_document(kb.id, doc.id)
    assert svc.search(kb.id, "删除", top_k=3) == []
    assert registry.get_document(kb.id, doc.id) is None
