"""pytest 共享 fixtures：测试隔离基座 + 本地假 LLM HTTP 服务（零网络依赖，带调用计数）。

隔离基座（审计 F1/F13，docs/quality.md P0-4）：
- 清除全部 RAG_* 环境变量，防止机器环境污染测试
- cwd 移入临时目录，防止项目根 .env 被误读
- 全局配置缓存复位（get_settings lru_cache）
- 外联护栏：除 127.0.0.1 外的一切 httpx 请求直接失败
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.config import Settings, get_settings

API_KEY = "test-key-0123456789abcdef"


class FakeLLMHandler(BaseHTTPRequestHandler):
    """可编程假 LLM：测试通过 RequestHandlerClass 类属性设置响应。"""

    response_body = b"{}"
    response_status = 200
    calls = 0

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_llm_server():
    FakeLLMHandler.calls = 0
    server = HTTPServer(("127.0.0.1", 0), FakeLLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


class FakeSiteHandler(BaseHTTPRequestHandler):
    """可编程假网站（SSRF/URL 摄取测试用）。"""

    body = b"<html><head><title>T</title></head><body><p>default</p></body></html>"
    status = 200
    location = None

    def do_GET(self):
        if self.location:
            self.send_response(302)
            self.send_header("Location", self.location)
            self.end_headers()
            return
        self.send_response(self.status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_site():
    FakeSiteHandler.location = None
    FakeSiteHandler.status = 200
    FakeSiteHandler.body = (
        "<html><head><title>测试</title></head><body><p>正文内容。</p>"
        "<script>bad()</script></body></html>"
    ).encode()
    server = HTTPServer(("127.0.0.1", 0), FakeSiteHandler)
    server.url = f"http://127.0.0.1:{server.server_port}/page"  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client(tmp_path, fake_llm_server, auth_headers):
    """标准 API 集成测试客户端：stub 嵌入 + 本地 Qdrant + SQLite + 假 LLM + 认证。"""
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_url=None,
        qdrant_path=tmp_path / "qdrant",
        database_url=f"sqlite:///{tmp_path / 'registry.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        llm_base_url=f"http://127.0.0.1:{fake_llm_server.server_port}/v1",
        llm_api_key="",
        llm_model="qwen-test",
        refusal_threshold=0.25,
        api_key=API_KEY,
    )
    app = create_app(settings)
    with TestClient(app, headers=auth_headers) as c:
        yield c


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch, tmp_path):
    """环境隔离：清除 RAG_* 环境变量、脱离项目 .env、复位配置缓存。"""
    for key in [k for k in os.environ if k.startswith("RAG_")]:
        monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)  # env_file 为相对路径，tmp 目录中无 .env
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def block_external_http(respx_mock):
    """外联护栏：127.0.0.1 直通（本地假服务），其余 httpx 请求直接失败（fail-fast）。

    respx 默认断言所有请求必须命中路由，故回环地址需显式 pass_through。
    """
    respx_mock.route(host__regex=r"^127\.0\.0\.1$").pass_through()
    respx_mock.route(host__regex=r"^(?!127\.0\.0\.1).*$").mock(
        side_effect=RuntimeError("测试中禁止访问外部网络（审计 F13 护栏）")
    )
    yield
