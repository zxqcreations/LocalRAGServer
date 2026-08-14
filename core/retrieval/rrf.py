"""RRF（Reciprocal Rank Fusion）纯函数（架构 §6：dense 与 sparse 候选集融合）。"""


def reciprocal_rank_fusion(ranked_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """融合多个有序候选列表为 id → RRF 分数。

    score = Σ_lists 1/(k + rank)，rank 从 1 起；同列表内重复 id 取最高排位；
    分数越高越相关，调用方按分数降序取 top-N。
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        # 先按首次出现去重，rank 基于去重后的位次
        deduped: list[str] = []
        seen: set[str] = set()
        for doc_id in ranked:
            if doc_id not in seen:
                seen.add(doc_id)
                deduped.append(doc_id)
        for rank, doc_id in enumerate(deduped, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores
