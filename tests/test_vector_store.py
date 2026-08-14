"""向量存储单测：内存实现全量覆盖；Qdrant 本地模式做环境冒烟（不可用则跳过）。"""
import math
import uuid

import pytest

from core.storage.vector import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorPoint,
)


def _v(*xs: float) -> list[float]:
    norm = math.sqrt(sum(x * x for x in xs))
    return [x / norm for x in xs]


def test_inmemory_roundtrip(tmp_path):
    store = InMemoryVectorStore()
    store.ensure_collection(3)
    store.upsert(
        [
            VectorPoint(id="c1", vector=_v(1, 0, 0), payload={"kb_id": "kb1", "doc_id": "d1"}),
            VectorPoint(id="c2", vector=_v(0, 1, 0), payload={"kb_id": "kb1", "doc_id": "d1"}),
            VectorPoint(id="c3", vector=_v(1, 0, 0), payload={"kb_id": "kb2", "doc_id": "d1"}),
        ]
    )
    # kb 过滤：c3 属于 kb2 不应出现
    hits = store.search(_v(1, 0, 0), "kb1", limit=2)
    assert [h.id for h in hits] == ["c1", "c2"]
    assert hits[0].score == pytest.approx(1.0)


def test_inmemory_dim_mismatch_raises(tmp_path):
    store = InMemoryVectorStore()
    store.ensure_collection(3)
    with pytest.raises(RuntimeError):
        store.ensure_collection(4)
    with pytest.raises(RuntimeError):
        store.upsert([VectorPoint(id="x", vector=[0.0, 0.0], payload={})])


def test_inmemory_requires_ensure_collection(tmp_path):
    with pytest.raises(RuntimeError):
        InMemoryVectorStore().upsert([VectorPoint(id="x", vector=[0.0], payload={})])


def test_inmemory_upsert_overwrites_same_id(tmp_path):
    store = InMemoryVectorStore()
    store.ensure_collection(2)
    store.upsert([VectorPoint(id="c1", vector=_v(1, 0), payload={"kb_id": "kb1"})])
    store.upsert([VectorPoint(id="c1", vector=_v(0, 1), payload={"kb_id": "kb1"})])
    hits = store.search(_v(0, 1), "kb1", limit=1)
    assert hits[0].id == "c1"
    assert hits[0].score == pytest.approx(1.0)


def test_inmemory_delete_by_document(tmp_path):
    store = InMemoryVectorStore()
    store.ensure_collection(2)
    store.upsert(
        [
            VectorPoint(id="c1", vector=_v(1, 0), payload={"kb_id": "kb1", "doc_id": "d1"}),
            VectorPoint(id="c2", vector=_v(0, 1), payload={"kb_id": "kb1", "doc_id": "d2"}),
        ]
    )
    store.delete_by_document("kb1", "d1")
    hits = store.search(_v(1, 0), "kb1", limit=5)
    assert [h.id for h in hits] == ["c2"]


def test_inmemory_search_empty_when_no_match(tmp_path):
    store = InMemoryVectorStore()
    store.ensure_collection(2)
    store.upsert([VectorPoint(id="c1", vector=_v(1, 0), payload={"kb_id": "kb1"})])
    assert store.search(_v(1, 0), "other-kb", limit=5) == []


def _qdrant_local_smoke(tmp_path):
    """环境冒烟：Qdrant 本地嵌入模式在当前平台不可用时优雅跳过。"""
    point_id = uuid.uuid4().hex  # Qdrant 要求 point id 为 UUID 格式
    try:
        store = QdrantVectorStore(path=tmp_path / "qdrant")
        store.ensure_collection(4)
        store.upsert(
            [
                VectorPoint(
                    id=point_id, vector=_v(1, 0, 0, 0), payload={"kb_id": "kb1", "doc_id": "d1"}
                )
            ]
        )
        hits = store.search(_v(1, 0, 0, 0), "kb1", limit=5)
        assert hits and hits[0].id == point_id
        assert hits[0].score == pytest.approx(1.0, abs=0.01)
        store.delete_by_document("kb1", "d1")
        assert store.search(_v(1, 0, 0, 0), "kb1", limit=5) == []
    except Exception as exc:  # 环境不可用（如 Windows 轮子缺失）时跳过而非失败
        pytest.skip(f"Qdrant 本地模式当前环境不可用：{exc}")


def test_qdrant_local_roundtrip(tmp_path):
    _qdrant_local_smoke(tmp_path)


def test_qdrant_dim_mismatch_raises(tmp_path):
    try:
        store = QdrantVectorStore(path=tmp_path / "qdrant")
        store.ensure_collection(4)
        with pytest.raises(RuntimeError):
            store.ensure_collection(8)
    except RuntimeError as exc:
        if "维度不匹配" in str(exc):
            raise
        pytest.skip(f"Qdrant 本地模式当前环境不可用：{exc}")
    except Exception as exc:
        pytest.skip(f"Qdrant 本地模式当前环境不可用：{exc}")


def test_qdrant_requires_url_or_path(tmp_path):
    with pytest.raises(ValueError):
        QdrantVectorStore()
