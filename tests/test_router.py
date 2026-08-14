"""查询路由测试（审计 F6：文档/代码/网页类型路由）。"""
from core.retrieval.router import RetrievalParams, route


def test_route_document_default():
    params = route("document")
    assert params == RetrievalParams(dense_limit=50, sparse_limit=50, rrf_k=60)


def test_route_code_boosts_sparse():
    # 代码库：稀疏（BM25 标识符匹配）优先
    params = route("code")
    assert params.sparse_limit > params.dense_limit


def test_route_web_boosts_dense():
    params = route("web")
    assert params.dense_limit > params.sparse_limit


def test_route_unknown_falls_back_to_document():
    assert route("nope") == route("document")
