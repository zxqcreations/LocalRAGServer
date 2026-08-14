"""评测集与检索回归测试（审计 F9/ARC-015）。"""
from eval.dataset import MIN_ENTRIES, load_qa, validate_dataset
from eval.run_retrieval import evaluate


def test_dataset_valid_and_self_consistent():
    entries = load_qa()
    errors = validate_dataset(entries)
    assert not errors, f"评测集校验失败：{errors}"


def test_dataset_size_and_stratification():
    entries = load_qa()
    assert len(entries) >= MIN_ENTRIES
    assert {e.kb_type for e in entries} == {"document", "code", "web"}
    assert any(e.is_hard for e in entries), "评测集须包含 hard 条目（相似干扰项）"


def test_dataset_ids_unique():
    entries = load_qa()
    ids = [e.id for e in entries]
    assert len(ids) == len(set(ids))


def test_retrieval_regression_metrics_are_sound():
    # 种子集自洽性：stub（哈希袋）嵌入实测 recall@10≈0.88，下限取 0.85 防回归；
    # 权威基线在 Phase 2 接入真实 bge-m3 后重定（见 eval/README.md）
    metrics = evaluate(top_k=10)
    assert metrics["recall@10"] >= 0.85, f"种子集 recall 过低：{metrics}"
    assert 0.0 <= metrics["mrr@10"] <= 1.0
    for m in metrics["per_type"].values():
        assert m["total"] > 0


def test_baseline_gate_passes_with_current_metrics():
    from eval.run_retrieval import check_baseline, evaluate

    metrics = evaluate(top_k=10)
    ok, message = check_baseline(metrics)
    assert ok, f"基线门禁应通过：{message}"


def test_baseline_gate_blocks_regression():
    from eval.run_retrieval import check_baseline

    ok, message = check_baseline({"recall@10": 0.5, "mrr@10": 0.4})
    assert not ok
    assert "recall@10 下降" in message
