"""拒答策略测试（审计 F12：三分支边界 + 空结果 + hard-negative）。"""
import pytest

from core.generation.refusal import refusal_field, should_refuse
from core.retrieval.search import SearchResult


def _result(dense: float, score: float = 0.0) -> SearchResult:
    return SearchResult(
        chunk_id="c",
        doc_id="d",
        doc_title="t",
        score=score,
        dense_score=dense,
        content="内容",
        expanded_content="内容",
    )


@pytest.mark.parametrize(
    "dense,expected",
    [
        (0.249, True),  # 低于阈值 → 拒答
        (0.250, False),  # 等于阈值 → 放行（判定语义：best < threshold 才拒答）
        (0.251, False),  # 高于阈值 → 放行
    ],
)
def test_refusal_three_way_boundary(dense, expected):
    # 审计 F12 三分支：0.249/0.250/0.251 边界行为
    threshold = 0.25
    assert should_refuse([_result(dense=dense)], threshold) is expected


def test_empty_results_always_refuse():
    assert should_refuse([], 0.25) is True


def test_hard_negative_with_low_rerank_score_refuses():
    # 审计 F12.4：rerank 激活时看 cross-encoder 分数——高分 dense 但 rerank 低分 → 拒答
    results = [_result(dense=0.9, score=0.05)]
    assert should_refuse(results, 0.25, field="score") is True


def test_refusal_field_selection():
    assert refusal_field(rerank_active=False) == "dense_score"
    assert refusal_field(rerank_active=True) == "score"
