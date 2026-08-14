"""拒答判定（架构 §7；审计 F12 三分支 + hard-negative）。

分数字段语义（与量纲解耦，审计 ARC-014）：
- rerank 未激活：用 dense_score（余弦相似度，量纲稳定）
- rerank 激活：用 score（cross-encoder 相关度；阈值需按评测集校准，Phase 2 评测工作）
"""
from core.retrieval.search import SearchResult


def should_refuse(
    results: list[SearchResult], threshold: float, field: str = "dense_score"
) -> bool:
    """最高分低于阈值即拒答（空结果恒拒答）。"""
    best = max((getattr(r, field) for r in results), default=0.0)
    return best < threshold


def refusal_field(rerank_active: bool) -> str:
    """按重排是否激活选择分数字段。"""
    return "score" if rerank_active else "dense_score"
