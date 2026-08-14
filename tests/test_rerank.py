"""重排层测试（审计 F6：假重排与真实重排分测；降级路径）。"""
import pytest

from core.config import Settings
from core.retrieval.rerank import NoopReranker, Reranker, build_reranker


class FakeReranker:
    """确定性假重排：按文档中是否含目标词打分（审计 F6 的假重排手法）。"""

    def __init__(self, keyword: str = "相关") -> None:
        self._keyword = keyword

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [2.0 if self._keyword in doc else 0.5 for doc in documents]


def test_noop_reranker_preserves_order_semantics():
    # 降级路径：分数全 1，调用方按原序处理
    r = NoopReranker()
    assert r.rerank("q", ["a", "b", "c"]) == [1.0, 1.0, 1.0]
    assert r.rerank("q", []) == []


def test_fake_reranker_scores_deterministically():
    r = FakeReranker(keyword="量子")
    scores = r.rerank("量子是什么", ["量子计算简介", "天气预报", "量子比特原理"])
    assert scores == [2.0, 0.5, 2.0]


def test_fake_reranker_satisfies_protocol():
    assert isinstance(FakeReranker(), Reranker)
    assert isinstance(NoopReranker(), Reranker)


def test_build_reranker_off_default():
    settings = Settings(rerank_backend="off")
    assert isinstance(build_reranker(settings), NoopReranker)


def test_build_reranker_unknown_backend_raises():
    settings = Settings(rerank_backend="nope")
    with pytest.raises(ValueError):
        build_reranker(settings)
