"""纯 Python BM25 稀疏检索（ADR-004：本地路径；生产切 Qdrant 原生 full-text，Phase 6）。

tokenizer：拉丁字符序列按词、CJK 按单字符——中英混合语料可用的简单形态。
内存倒排索引；千万级规模不适用（见 ADR-004 约束）。
"""
import math
import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")


def tokenize(text: str) -> list[str]:
    """拉丁词 + CJK 单字符 tokenization。"""
    tokens: list[str] = []
    for match in _WORD_RE.finditer(text):
        tokens.append(match.group(0).lower())
    for ch in text:
        if _CJK_RE.match(ch):
            tokens.append(ch)
    return tokens


@dataclass(frozen=True)
class Bm25Hit:
    id: int  # 语料中的文档序号
    score: float


class BM25Retriever:
    """标准 Okapi BM25：score(q,d) = Σ idf(t) * tf_norm，k1=1.5, b=0.75。"""

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._docs: list[list[str]] = [tokenize(doc) for doc in corpus]
        self._n = len(corpus)
        self._doc_len = [len(doc) for doc in self._docs]
        self._avg_len = sum(self._doc_len) / self._n if self._n else 0.0
        self.df: dict[str, int] = {}
        for doc in self._docs:
            for term in set(doc):
                self.df[term] = self.df.get(term, 0) + 1

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 50) -> list[Bm25Hit]:
        query_terms = set(tokenize(query))
        if not query_terms or self._n == 0:
            return []
        idfs = {t: self.idf(t) for t in query_terms}
        scored: list[Bm25Hit] = []
        for doc_id, doc in enumerate(self._docs):
            term_freq: dict[str, int] = {}
            for term in doc:
                if term in query_terms:
                    term_freq[term] = term_freq.get(term, 0) + 1
            if not term_freq:
                continue
            score = 0.0
            for term, tf in term_freq.items():
                norm_tf = tf * (self._k1 + 1) / (
                    tf + self._k1 * (1 - self._b + self._b * self._doc_len[doc_id] / self._avg_len)
                )
                score += idfs[term] * norm_tf
            if score > 0:
                scored.append(Bm25Hit(id=doc_id, score=score))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]
