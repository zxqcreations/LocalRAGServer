"""RRF 融合纯函数测试（审计 F6：≥3 组手算期望值）。"""
import pytest

from core.retrieval.rrf import reciprocal_rank_fusion


def test_single_list_scores_are_hand_computed():
    # RRF: score = 1/(k + rank)，rank 从 1 起；k=60
    # rank1: 1/61 ≈ 0.0163934, rank2: 1/62 ≈ 0.0161290, rank3: 1/63 ≈ 0.0158730
    scores = reciprocal_rank_fusion([["a", "b", "c"]], k=60)
    assert scores["a"] == pytest.approx(1 / 61, abs=1e-6)
    assert scores["b"] == pytest.approx(1 / 62, abs=1e-6)
    assert scores["c"] == pytest.approx(1 / 63, abs=1e-6)


def test_overlap_sums_both_reciprocal_ranks():
    # a 在列表1排第1（1/61）、在列表2排第2（1/62）→ 0.0163934 + 0.0161290
    scores = reciprocal_rank_fusion([["a", "b"], ["x", "a"]], k=60)
    expected = 1 / 61 + 1 / 62
    assert scores["a"] == pytest.approx(expected, abs=1e-6)
    # b 仅在列表1排第2
    assert scores["b"] == pytest.approx(1 / 62, abs=1e-6)
    # x 仅在列表2排第1
    assert scores["x"] == pytest.approx(1 / 61, abs=1e-6)


def test_k_parameter_scales_scores():
    # 更大的 k 压缩分数差距
    s60 = reciprocal_rank_fusion([["a", "b"]], k=60)
    s100 = reciprocal_rank_fusion([["a", "b"]], k=100)
    assert s60["a"] > s100["a"]
    assert s100["a"] == pytest.approx(1 / 101, abs=1e-6)


def test_empty_and_none_inputs():
    assert reciprocal_rank_fusion([]) == {}
    assert reciprocal_rank_fusion([[], ["a"]])["a"] == pytest.approx(1 / 61, abs=1e-6)


def test_duplicate_id_within_one_list_uses_best_rank():
    # 同一 id 在单列表中出现两次：取最高排位（第一个）
    scores = reciprocal_rank_fusion([["a", "a", "b"]], k=60)
    assert scores["a"] == pytest.approx(1 / 61, abs=1e-6)
    assert scores["b"] == pytest.approx(1 / 62, abs=1e-6)
