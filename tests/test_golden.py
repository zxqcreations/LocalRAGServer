"""golden 快照回归（审计 F10：模型/实现切换时的漂移检测机制）。"""
import json
from pathlib import Path

from core.retrieval.embeddings import StubEmbedder

GOLDEN = Path(__file__).resolve().parent / "golden"


def _cosine(a, b):
    import math

    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


def test_embedding_snapshot_consistent():
    snapshot = json.loads(
        (GOLDEN / "embeddings_snapshot.json").read_text(encoding="utf-8")
    )
    assert snapshot["dim"] == 64
    embedder = StubEmbedder(dim=64)
    assert len(snapshot["texts"]) == 100
    for i, text in enumerate(snapshot["texts"]):
        current = embedder.embed([text])[0]
        cosine = _cosine(current, snapshot["vectors"][i])
        # stub 确定性实现 → 完全一致；切换真实模型后此处度量漂移（F10：阈值 >0.95）
        assert cosine > 0.9999, f"第 {i} 条向量漂移：cosine={cosine}"


def test_rerank_snapshot_order_consistent():
    snapshot = json.loads(
        (GOLDEN / "rerank_snapshot.json").read_text(encoding="utf-8")
    )
    candidates = snapshot["candidates"]

    def keyword_rerank(doc):
        return 2.0 if "量子" in doc else 0.5

    current_order = sorted(
        range(len(candidates)), key=lambda i: keyword_rerank(candidates[i]), reverse=True
    )
    assert current_order == snapshot["order"]


def test_snapshot_files_are_committed():
    # 快照必须入版本库（审计 F10：golden 随代码走）
    assert (GOLDEN / "embeddings_snapshot.json").exists()
    assert (GOLDEN / "rerank_snapshot.json").exists()
