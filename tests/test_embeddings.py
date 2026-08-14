"""嵌入后端单测：stub 确定性/规范化/维度；TEI/OpenAI 后端用假 HTTP 服务（零网络）。"""
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from core.config import Settings
from core.retrieval.embeddings import (
    OpenAIEmbedder,
    StubEmbedder,
    TeiEmbedder,
    build_embedder,
)


def test_stub_is_deterministic_and_normalized():
    e = StubEmbedder(dim=64)
    first = e.embed(["量子计算"])[0]
    second = e.embed(["量子计算"])[0]
    assert first == second
    assert math.sqrt(sum(x * x for x in first)) == pytest.approx(1.0)


def test_stub_different_texts_differ():
    e = StubEmbedder(dim=64)
    a, b = e.embed(["苹果", "香蕉"])
    assert a != b


def test_stub_respects_dim():
    e = StubEmbedder(dim=128)
    assert all(len(v) == 128 for v in e.embed(["x", "yy"]))


def test_stub_empty_text_is_zero_vector():
    e = StubEmbedder(dim=8)
    v = e.embed([""])[0]
    assert all(x == 0.0 for x in v)


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_stub_has_semantic_structure():
    # 审计 F5 核心性质：相似文本余弦高、无关文本余弦低（检索测试的可信地基）
    e = StubEmbedder(dim=256)
    a = e.embed(["量子计算使用量子比特与叠加态。"])[0]
    similar = e.embed(["量子计算使用量子比特。"])[0]
    unrelated = e.embed(["今天的股票市场行情分析报告。"])[0]
    assert _cosine(a, similar) > 0.5
    assert _cosine(a, unrelated) < 0.3


def test_build_embedder_stub_from_settings():
    settings = Settings(embedding_backend="stub", embedding_dim=64)
    e = build_embedder(settings)
    assert isinstance(e, StubEmbedder)
    assert e.dim == 64


def test_build_embedder_unknown_backend_raises():
    with pytest.raises(ValueError):
        settings = Settings(embedding_backend="nope")  # Settings 构造即 fail-fast
        build_embedder(settings)


# ---------- 假嵌入 HTTP 服务 ----------


class _EmbedHandler(BaseHTTPRequestHandler):
    response_body = b"{}"
    response_status = 200
    requests = []  # 记录请求：{path, auth, body}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
            }
        )
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *args):
        pass


@pytest.fixture
def embed_server():
    _EmbedHandler.requests = []
    server = HTTPServer(("127.0.0.1", 0), _EmbedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def _set_body(server, payload):
    server.RequestHandlerClass.response_status = 200
    server.RequestHandlerClass.response_body = json.dumps(payload).encode("utf-8")


# ---------- TEI 后端 ----------


def test_tei_embedder_roundtrip(embed_server):
    _set_body(embed_server, [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    e = TeiEmbedder(url=f"http://127.0.0.1:{embed_server.server_port}", dim=3)
    vecs = e.embed(["甲", "乙"])
    assert len(vecs) == 2 and all(len(v) == 3 for v in vecs)
    req = embed_server.RequestHandlerClass.requests[-1]
    assert req["path"] == "/embed"
    assert req["body"]["inputs"] == ["甲", "乙"]
    assert req["body"]["normalize"] is True


def test_tei_embedder_dim_mismatch_raises(embed_server):
    _set_body(embed_server, [[0.1, 0.2]])  # 返回 2 维 ≠ 配置 3 维
    e = TeiEmbedder(url=f"http://127.0.0.1:{embed_server.server_port}", dim=3)
    with pytest.raises(RuntimeError):
        e.embed(["甲"])


def test_build_embedder_tei_from_settings():
    settings = Settings(embedding_backend="tei", embedding_dim=4, tei_url="http://x:9002")
    e = build_embedder(settings)
    assert isinstance(e, TeiEmbedder)
    assert e.dim == 4


# ---------- OpenAI 兼容后端 ----------


def test_openai_embedder_roundtrip_and_v1_path(embed_server):
    _set_body(
        embed_server,
        {"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
    )
    e = OpenAIEmbedder(
        base_url=f"http://127.0.0.1:{embed_server.server_port}/v1",
        api_key="test-key",
        model="bge-m3",
        dim=2,
    )
    vecs = e.embed(["甲", "乙"])
    assert len(vecs) == 2 and all(len(v) == 2 for v in vecs)
    req = embed_server.RequestHandlerClass.requests[-1]
    assert req["path"] == "/v1/embeddings"  # 归一化后始终走 /v1 前缀
    assert req["auth"] == "Bearer test-key"
    assert req["body"]["model"] == "bge-m3"


def test_openai_embedder_dim_mismatch_raises(embed_server):
    _set_body(embed_server, {"data": [{"embedding": [0.1]}]})
    e = OpenAIEmbedder(
        base_url=f"http://127.0.0.1:{embed_server.server_port}",
        api_key="",
        model="m",
        dim=2,
    )
    with pytest.raises(RuntimeError):
        e.embed(["甲"])


def test_build_embedder_openai_from_settings():
    settings = Settings(embedding_backend="openai", embedding_dim=8, openai_api_key="k")
    e = build_embedder(settings)
    assert isinstance(e, OpenAIEmbedder)
    assert e.dim == 8
