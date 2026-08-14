"""生成 golden 快照（审计 F10：嵌入向量 + 重排排序，模型/实现切换时对比回归）。

当前口径：stub 嵌入（dim=64，确定性）与 KeywordReranker——机制验证用；
真实 bge-m3 / bge-reranker 快照在 Phase 3 GPU 验证时重生成并覆盖本文件输出。

用法：uv run python scripts/golden/gen_golden.py
"""
import json
import sys
import time
from pathlib import Path

from core.retrieval.embeddings import StubEmbedder

OUT = Path(__file__).resolve().parents[2] / "tests" / "golden"

# 固定中英混合语料（确定性编号句式）
EMBED_TEXTS = [
    f"量子计算第{i}号文档：量子比特与叠加态原理，Quantum qubit superposition."
    for i in range(50)
] + [
    f"Machine learning doc {i}: gradient descent and regularization."
    for i in range(50)
]

RERANK_CANDIDATES = [
    "量子比特退相干问题的实验研究",
    "股票市场量化交易策略分析",
    "量子纠缠在通信中的应用",
    "天气预报与气候模型",
    "量子计算的肖尔算法",
] * 4  # 20 条


class KeywordReranker:
    """与 tests/test_rerank.py 同构的确定性重排（"量子"关键词打分）。"""

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [2.0 if "量子" in doc else 0.5 for doc in documents]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    embedder = StubEmbedder(dim=64)
    vectors = embedder.embed(EMBED_TEXTS)

    embed_snapshot = {
        "embedder": "stub",
        "dim": 64,
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "texts": EMBED_TEXTS,
        "vectors": vectors,
    }
    (OUT / "embeddings_snapshot.json").write_text(
        json.dumps(embed_snapshot, ensure_ascii=False), encoding="utf-8"
    )

    reranker = KeywordReranker()
    scores = reranker.rerank("量子", RERANK_CANDIDATES)
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    rerank_snapshot = {
        "reranker": "keyword(量子)",
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "candidates": RERANK_CANDIDATES,
        "order": order,
    }
    (OUT / "rerank_snapshot.json").write_text(
        json.dumps(rerank_snapshot, ensure_ascii=False), encoding="utf-8"
    )
    print(f"golden 快照已写入：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
