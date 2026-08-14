"""向量存储抽象（Repository 模式）。

- QdrantVectorStore：生产实现（远程服务或本地嵌入模式），named vector "dense"
- InMemoryVectorStore：确定性内存实现，供测试与无依赖开发场景

Payload 保持最小化（kb_id / doc_id / chunk_index），chunk 正文唯一存放在注册表，
检索命中后按 chunk_id 回查正文——与架构文档 §8.2 一致。
"""
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    HnswConfigDiff,
    MatchValue,
    PointStruct,
    SearchParams,
    VectorParams,
)

COLLECTION = "chunks"
VECTOR_NAME = "dense"


@dataclass(frozen=True)
class VectorPoint:
    id: str
    vector: list[float]
    payload: dict


@dataclass(frozen=True)
class ScoredPoint:
    id: str
    score: float
    payload: dict


@runtime_checkable
class VectorStore(Protocol):
    def ensure_collection(self, dim: int) -> None: ...

    def upsert(self, points: list[VectorPoint]) -> None: ...

    def search(self, vector: list[float], kb_id: str, limit: int) -> list[ScoredPoint]: ...

    def delete_by_document(self, kb_id: str, doc_id: str) -> None: ...


def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


class InMemoryVectorStore:
    """线性扫描 + 余弦相似度；确定性、零外部依赖，供测试与开发。"""

    def __init__(self) -> None:
        self._points: dict[str, VectorPoint] = {}
        self._dim: int | None = None

    def ensure_collection(self, dim: int) -> None:
        if self._dim is not None and self._dim != dim:
            raise RuntimeError(f"向量维度不匹配：已存在 {self._dim} 维，收到 {dim} 维")
        self._dim = dim

    def upsert(self, points: list[VectorPoint]) -> None:
        if self._dim is None:
            raise RuntimeError("集合未初始化：先调用 ensure_collection")
        for p in points:
            if len(p.vector) != self._dim:
                raise RuntimeError(f"向量维度不匹配：期望 {self._dim}，收到 {len(p.vector)}")
            self._points[p.id] = p

    def search(self, vector: list[float], kb_id: str, limit: int) -> list[ScoredPoint]:
        scored = [
            ScoredPoint(id=p.id, score=_cosine(vector, p.vector), payload=p.payload)
            for p in self._points.values()
            if p.payload.get("kb_id") == kb_id
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:limit]

    def delete_by_document(self, kb_id: str, doc_id: str) -> None:
        self._points = {
            pid: p
            for pid, p in self._points.items()
            if not (p.payload.get("kb_id") == kb_id and p.payload.get("doc_id") == doc_id)
        }


class QdrantVectorStore:
    """Qdrant 实现：url 远程服务；path 本地嵌入模式（免 Docker，落盘指定目录）。"""

    def __init__(
        self,
        url: str | None = None,
        path: str | Path | None = None,
        hnsw_m: int = 16,
        hnsw_ef_construct: int = 200,
        hnsw_ef: int = 256,
    ) -> None:
        if url:
            self._client = QdrantClient(url=url)
        elif path:
            self._client = QdrantClient(path=str(path))
        else:
            raise ValueError("QdrantVectorStore 需要 url 或 path 至少一个")
        self._hnsw_m = hnsw_m
        self._hnsw_ef_construct = hnsw_ef_construct
        self._hnsw_ef = hnsw_ef

    def ensure_collection(self, dim: int) -> None:
        if self._client.collection_exists(COLLECTION):
            vectors = self._client.get_collection(COLLECTION).config.params.vectors
            existing: int | None = None
            if isinstance(vectors, dict):
                existing = vectors[VECTOR_NAME].size
            elif vectors is not None:
                existing = vectors.size
            if existing is not None and existing != dim:
                raise RuntimeError(
                    f"集合维度不匹配：已有 {existing} 维，收到 {dim} 维；"
                    "切换嵌入模型请新建集合（架构 §8.2）"
                )
            return
        self._client.create_collection(
            collection_name=COLLECTION,
            vectors_config={
                VECTOR_NAME: VectorParams(
                    size=dim,
                    distance=Distance.COSINE,
                    hnsw_config=HnswConfigDiff(
                        m=self._hnsw_m, ef_construct=self._hnsw_ef_construct
                    ),
                )
            },
        )

    def upsert(self, points: list[VectorPoint]) -> None:
        self._client.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(id=p.id, vector={VECTOR_NAME: p.vector}, payload=p.payload)
                for p in points
            ],
            wait=True,
        )

    def search(self, vector: list[float], kb_id: str, limit: int) -> list[ScoredPoint]:
        response = self._client.query_points(
            collection_name=COLLECTION,
            query=vector,
            using=VECTOR_NAME,  # named vectors 模式必须显式指定查询向量名
            query_filter=Filter(must=[FieldCondition(key="kb_id", match=MatchValue(value=kb_id))]),
            search_params=SearchParams(hnsw_ef=self._hnsw_ef),
            limit=limit,
        )
        return [
            ScoredPoint(id=str(pt.id), score=pt.score, payload=pt.payload or {})
            for pt in response.points
        ]

    def delete_by_document(self, kb_id: str, doc_id: str) -> None:
        self._client.delete(
            collection_name=COLLECTION,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(key="kb_id", match=MatchValue(value=kb_id)),
                        FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
                    ]
                )
            ),
            wait=True,
        )

    def close(self) -> None:
        """释放本地嵌入模式的资源（进程退出前调用）。"""
        self._client.close()
