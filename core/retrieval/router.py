"""查询路由（架构 §6）：按 KB 类型参数化检索策略。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalParams:
    dense_limit: int
    sparse_limit: int
    rrf_k: int

    def with_limits(self, **overrides: int) -> "RetrievalParams":
        return RetrievalParams(
            dense_limit=overrides.get("dense_limit", self.dense_limit),
            sparse_limit=overrides.get("sparse_limit", self.sparse_limit),
            rrf_k=overrides.get("rrf_k", self.rrf_k),
        )


_DEFAULTS = {
    "document": RetrievalParams(dense_limit=50, sparse_limit=50, rrf_k=60),
    # 代码 KB：稀疏侧权重更高——BM25 对标识符/符号天然敏感（tokenizer 保留 [a-zA-Z0-9_]+）
    "code": RetrievalParams(dense_limit=30, sparse_limit=70, rrf_k=60),
    # 网页 KB：正文噪声多，dense 语义优先
    "web": RetrievalParams(dense_limit=70, sparse_limit=30, rrf_k=60),
}


def route(kb_type: str) -> RetrievalParams:
    """KB 类型 → 检索参数；未知类型回退 document 策略。"""
    return _DEFAULTS.get(kb_type, _DEFAULTS["document"])
