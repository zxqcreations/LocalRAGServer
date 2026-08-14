"""重排层（架构 §6/§7）：Reranker Protocol + 真实/降级实现。

- NoopReranker：rerank_backend=off 的降级路径（保持原序，分数全 1）
- CrossEncoderReranker：local 后端（bge-reranker-v2-m3，GPU/CPU 自动）
- tei 后端留待 Phase 6（与 TEI 部署同步）
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class NoopReranker:
    """降级路径（rerank_backend=off）：不重排，仅保持原序。"""

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [1.0] * len(documents)


class CrossEncoderReranker:
    """local 后端：sentence-transformers CrossEncoder（需 embed extra 与 GPU torch）。"""

    def __init__(self, model_name: str) -> None:  # pragma: no cover
        from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]

        self._model = CrossEncoder(model_name)

    def rerank(self, query: str, documents: list[str]) -> list[float]:  # pragma: no cover
        if not documents:
            return []
        scores = self._model.predict([(query, doc) for doc in documents])
        return [float(s) for s in scores]


def build_reranker(settings) -> Reranker:
    if settings.rerank_backend == "off":
        return NoopReranker()
    if settings.rerank_backend == "local":
        return CrossEncoderReranker(settings.rerank_model)
    raise ValueError(f"未知重排后端：{settings.rerank_backend}（可选 off | local）")
