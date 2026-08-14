"""审计与限流测试（F-05 只追加落库；ADR-005 分层配额 429）。"""
from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.config import Settings
from tests.conftest import API_KEY


def test_record_audit_and_list(tmp_path):
    from core.storage.registry import Registry

    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    registry.record_audit(actor="master", action="search", kb_id="kb1", ip="127.0.0.1")
    registry.record_audit(actor="key1", action="delete", kb_id="kb1")
    logs = registry.list_audit(limit=10)
    assert len(logs) == 2
    assert logs[0].action == "delete"  # 最新在前
    assert logs[1].action == "search"


def test_search_generates_audit_record(tmp_path, fake_llm_server, auth_headers):
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "qdrant",
        database_url=f"sqlite:///{tmp_path / 'r.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        api_key=API_KEY,
        llm_base_url=f"http://127.0.0.1:{fake_llm_server.server_port}/v1",
        llm_model="qwen-test",
    )
    app = create_app(settings)
    with TestClient(app, headers=auth_headers) as c:
        kb_id = c.post("/api/v1/kb", json={"name": "库"}).json()["data"]["id"]
        c.post(
            f"/api/v1/kb/{kb_id}/documents",
            files={"file": ("a.md", "内容。".encode(), "text/markdown")},
        )
        c.post("/api/v1/search", json={"query": "内容", "kb_id": kb_id})
    logs = app.state.registry.list_audit(limit=10)
    actions = {log.action for log in logs}
    assert "search" in actions
    assert "ingest" in actions


def test_rate_limit_returns_429(tmp_path, fake_llm_server, auth_headers):
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "qdrant",
        database_url=f"sqlite:///{tmp_path / 'r.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        api_key=API_KEY,
        llm_base_url=f"http://127.0.0.1:{fake_llm_server.server_port}/v1",
        llm_model="qwen-test",
    )
    app = create_app(settings)
    with TestClient(app, headers=auth_headers) as c:
        kb_id = c.post("/api/v1/kb", json={"name": "库"}).json()["data"]["id"]
        statuses = [
            c.post("/api/v1/search", json={"query": "x", "kb_id": kb_id}).status_code
            for _ in range(130)  # 主 Key 配额 120/min → 超出后 429
        ]
    assert 429 in statuses
    assert statuses.count(429) >= 1
