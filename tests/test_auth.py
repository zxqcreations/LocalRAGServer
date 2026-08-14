"""认证与上传防护集成测试（审计 F-01/F-07/F-09，docs/quality.md P0-6）。"""
import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.config import Settings

KEY = "test-secret-key-0123456789abcdef"


@pytest.fixture
def no_key_app(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "qdrant",
        database_url=f"sqlite:///{tmp_path / 'r.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        api_key="",  # 未配置 → fail-closed
    )
    return create_app(settings)


@pytest.fixture
def key_app(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "qdrant",
        database_url=f"sqlite:///{tmp_path / 'r.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        api_key=KEY,
    )
    return create_app(settings)


def test_fail_closed_when_no_key_configured(no_key_app):
    with TestClient(no_key_app) as client:
        # 健康检查始终开放
        assert client.get("/health").status_code == 200
        # 业务接口全部拒绝（fail-closed）
        resp = client.get("/api/v1/kb")
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "auth_unconfigured"
        resp = client.post("/api/v1/search", json={"query": "x", "kb_id": "k"})
        assert resp.status_code == 503


def test_missing_bearer_token_401(key_app):
    # 无默认认证头的客户端（httpx 请求头与客户端头合并，不能用空 dict 覆盖）
    with TestClient(key_app) as client:
        resp = client.get("/api/v1/kb")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_required"


def test_invalid_key_401(key_app):
    with TestClient(key_app) as client:
        resp = client.get("/api/v1/kb", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_invalid"


def test_valid_key_passes(key_app):
    with TestClient(key_app) as client:
        resp = client.get("/api/v1/kb", headers={"Authorization": f"Bearer {KEY}"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_health_never_requires_auth(no_key_app):
    with TestClient(no_key_app) as client:
        assert client.get("/health").json()["success"] is True
