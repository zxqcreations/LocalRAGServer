"""混合检索集成测试：dense + BM25 → RRF 融合（审计 F6）。"""
import pytest

from core.ingest.chunker import Chunk
from core.retrieval.embeddings import StubEmbedder
from core.retrieval.hybrid import HybridRetriever
from core.storage.registry import Registry
from core.storage.vector import InMemoryVectorStore, VectorPoint


@pytest.fixture
def env(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    store = InMemoryVectorStore()
    store.ensure_collection(64)
    embedder = StubEmbedder(dim=64)

    kb1 = registry.create_kb("库一")
    kb2 = registry.create_kb("库二")

    def add_doc(kb, title, text):
        doc = registry.create_document(kb.id, title, f"t://{title}", f"h-{title}")
        chunks = [
            Chunk(text=t, index=i, start=0, end=len(t))
            for i, t in enumerate(text.split("\n"))
            if t
        ]
        ids = registry.set_chunks(doc.id, kb.id, chunks)
        store.upsert(
            [
                VectorPoint(
                    id=cid,
                    vector=embedder.embed([t])[0],
                    payload={"kb_id": kb.id, "doc_id": doc.id},
                )
                for cid, t in zip(ids, [c.text for c in chunks], strict=True)
            ]
        )
        return doc

    add_doc(kb1, "quantum.md", "量子计算使用量子比特与叠加态\n天气晴朗适合出行")
    add_doc(kb1, "weather.md", "今日天气多云转晴\n量子比特退相干问题")
    add_doc(kb2, "other.md", "量子比特在其他知识库的文档")
    return registry, store, embedder, kb1, kb2


def test_hybrid_returns_dense_and_sparse_sources(env):
    registry, store, embedder, kb1, _ = env
    hybrid = HybridRetriever(store, registry, kb1.id)
    hits = hybrid.search("量子比特", embedder.embed(["量子比特"])[0], top_k=4)
    # kb1 共 4 个 chunk，全部可返回；融合分数为正
    assert len(hits) == 4
    assert all(h.score > 0 for h in hits)


def test_hybrid_kb_isolation(env):
    registry, store, embedder, kb1, kb2 = env
    hybrid = HybridRetriever(store, registry, kb1.id)
    hits = hybrid.search("量子比特", embedder.embed(["量子比特"])[0], top_k=10)
    # kb2 的 chunk 不在 kb1 的 BM25 索引中，也不在 dense 过滤内
    kb2_contents = [c for _, c in registry.list_chunks(kb2.id)]
    contents = registry.get_chunk_contents([h.chunk_id for h in hits])
    assert not any(c in kb2_contents for c in contents.values())


def test_hybrid_keyword_match_wins_when_dense_weak(env):
    registry, store, embedder, kb1, _ = env
    hybrid = HybridRetriever(store, registry, kb1.id)
    # 纯关键词查询：dense 语义弱，sparse 应把含"天气"的 chunk 拉到前列
    hits = hybrid.search("天气", embedder.embed(["天气"])[0], top_k=2)
    contents = registry.get_chunk_contents([h.chunk_id for h in hits])
    assert any("天气" in c for c in contents.values())


def test_hybrid_top_k_respected(env):
    registry, store, embedder, kb1, _ = env
    hybrid = HybridRetriever(store, registry, kb1.id)
    assert len(hybrid.search("量子", embedder.embed(["量子"])[0], top_k=1)) == 1
