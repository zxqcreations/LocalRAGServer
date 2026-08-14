"""BM25 检索器手算测试（ADR-004：稀疏侧本地实现）。"""
import math

import pytest

from core.retrieval.bm25 import BM25Retriever, tokenize

# ---------- tokenize ----------


def test_tokenize_mixed_content():
    # 拉丁按词、CJK 按字符
    tokens = tokenize("量子 computing 101")
    assert "量子" not in tokens  # CJK 逐字符
    assert "computing" in tokens
    assert "101" in tokens
    assert len(tokens) == 4  # 量、子、computing、101


# ---------- BM25 手算 ----------


def _corpus():
    # 文档0: "a b c"；文档1: "a a c"；文档2: "b c"
    return ["a b c", "a a c", "b c"]


def test_idf_hand_computed():
    # N=3；df(a)=2, df(b)=2, df(c)=3
    # idf = ln(1 + (N - df + 0.5) / (df + 0.5))
    bm = BM25Retriever(_corpus(), k1=1.5, b=0.75)
    df = bm.df
    assert df == {"a": 2, "b": 2, "c": 3}
    expected_idf_a = math.log(1 + (3 - 2 + 0.5) / (2 + 0.5))
    assert bm.idf("a") == pytest.approx(expected_idf_a)


def test_scoring_query_a_ranks_doc1_first():
    # 文档1 含两个 a（词频 2），文档0 含一个 a → 文档1 分数更高
    bm = BM25Retriever(_corpus(), k1=1.5, b=0.75)
    scores = bm.search("a", top_k=3)
    assert scores[0].id == 1
    assert scores[0].score > scores[1].score
    # 不含查询词的文档不返回
    assert all(s.score > 0 for s in scores)


def test_top_k_limit_and_ordering():
    bm = BM25Retriever(["x x x y", "y y", "z"], k1=1.5, b=0.75)
    hits = bm.search("y", top_k=2)
    assert len(hits) == 2
    assert [h.id for h in hits] == [1, 0]  # 词频 2 > 词频 1


def test_empty_corpus_and_query():
    assert BM25Retriever([]).search("x") == []
    bm = BM25Retriever(["a b c"])
    assert bm.search("") == []
