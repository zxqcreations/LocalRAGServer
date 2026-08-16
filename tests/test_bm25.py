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


# ---------- 倒排索引重构的一致性（对拍全扫描参考实现） ----------


def _reference_search(bm25_docs, query, k1=1.5, b=0.75, top_k=50):
    """旧版全扫描实现（评分公式逐项同序，作黄金参考）。"""
    import math

    n = len(bm25_docs)
    if n == 0:
        return []
    docs = [tokenize(d) for d in bm25_docs]
    doc_len = [len(d) for d in docs]
    avg_len = sum(doc_len) / n
    df: dict[str, int] = {}
    for d in docs:
        for term in set(d):
            df[term] = df.get(term, 0) + 1
    query_terms = set(tokenize(query))
    idfs = {
        t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in query_terms if df.get(t)
    }
    scored = []
    for doc_id, doc in enumerate(docs):
        tf: dict[str, int] = {}
        for term in doc:
            if term in idfs:
                tf[term] = tf.get(term, 0) + 1
        if not tf:
            continue
        score = sum(
            idfs[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * doc_len[doc_id] / avg_len))
            for t, f in tf.items()
        )
        scored.append((doc_id, score))
    scored.sort(key=lambda p: p[1], reverse=True)
    return scored[:top_k]


def test_posting_index_matches_reference_scores():
    corpus = [
        "量子计算 quantum computing 量子比特",
        "quantum 叠加态 superposition 量子",
        "波导放大器 gain medium 增益",
        "amplifier 波导 waveguide gain 放大器 增益",
        "经典力学 classical mechanics 牛顿",
        "量子 量子 量子 纠缠",
    ]
    for query in ["量子", "quantum gain", "波导放大器", "不存在词xyz", "a b c 量子"]:
        fast = BM25Retriever(corpus).search(query, top_k=50)
        ref = _reference_search(corpus, query)
        assert [h.id for h in fast] == [doc_id for doc_id, _ in ref]
        for hit, (_, ref_score) in zip(fast, ref, strict=True):
            assert hit.score == pytest.approx(ref_score, abs=1e-12)


def test_posting_index_deterministic_tie_order():
    # 同分文档按文档序升序（与全扫描实现的稳定排序语义一致）
    bm = BM25Retriever(["x y", "x y"], k1=1.5, b=0.75)
    hits = bm.search("x y", top_k=2)
    assert [h.id for h in hits] == [0, 1]


# ---------- 边界与查询成本钳制（安全审查 M / 代码审查 L3） ----------


def test_top_k_zero_and_single_doc():
    bm = BM25Retriever(["a b c"])
    assert bm.search("a", top_k=0) == []
    # 单文档语料：阈值钳制不生效（N < _HIGH_DF_MIN_N），正常命中
    hits = bm.search("a")
    assert [h.id for h in hits] == [0]
    assert hits[0].score > 0


def test_high_df_terms_skipped_in_large_corpus():
    # N=1000 语料：df=N 的词项贡献可忽略，跳过（与旧实现差量 ~1e-5）
    corpus = ["a"] * 999 + ["a b"]
    bm = BM25Retriever(corpus)
    assert bm.search("a") == []  # df(a)=1000 > 0.9×N → 跳过
    hits = bm.search("a b")
    assert [h.id for h in hits] == [999]  # 仅 "b" 有区分度


def test_posting_budget_capped(monkeypatch):
    # 恶意超长查询兜底：累计遍历上限按排序后的词序确定性截断
    import core.retrieval.bm25 as bm25_module

    corpus = ["aa bb", "bb cc", "cc dd"]
    monkeypatch.setattr(bm25_module, "_MAX_WALKED_POSTINGS", 2)
    bm = BM25Retriever(corpus)
    # 词序 aa < bb < cc：aa(1 文档) 累计 1 未超限；bb(2 文档) 累计 3 > 2 → bb 起截断
    hits = bm.search("aa bb cc")
    hit_ids = {h.id for h in hits}
    assert 0 in hit_ids  # aa 命中
    assert hit_ids <= {0, 1}  # bb/cc 的贡献被截断（文档 1/2 不出现）


def test_cross_seed_determinism():
    # 查询词排序消除 hash 种子漂移：不同 PYTHONHASHSEED 下输出一致
    import json
    import os
    import pathlib
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    code = (
        "import json, sys; sys.path.insert(0, '.'); "
        "from core.retrieval.bm25 import BM25Retriever; "
        "bm = BM25Retriever(['a b c d', 'a a c e', 'b b c f'] * 10); "
        "print(json.dumps([[h.id, round(h.score, 15)] for h in bm.search('c d a b')]))"
    )
    outputs = []
    for seed in ("1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(repo_root),
        )
        assert out.returncode == 0, out.stderr
        outputs.append(json.loads(out.stdout))
    assert outputs[0] == outputs[1] == outputs[2]
