"""API 级 ACL 泄漏矩阵（审计 F-13：A 租户 Key 查 B 租户数据必须 403，进 CI 永不豁免）。

测试客户端不带默认认证头（httpx 请求头与客户端头合并、无法移除）——
身份由每请求 headers 显式指定。
"""
import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.config import Settings
from tests.conftest import API_KEY


@pytest.fixture
def env(tmp_path, fake_llm_server):
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "qdrant",
        database_url=f"sqlite:///{tmp_path / 'r.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        api_key=API_KEY,  # 引导主 Key
        llm_base_url=f"http://127.0.0.1:{fake_llm_server.server_port}/v1",
        llm_model="qwen-test",
    )
    app = create_app(settings)
    # 设置期客户端用完即关（避免与测试客户端生命周期重叠导致 Qdrant 目录锁冲突）
    with TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"}) as master:
        kb1 = master.post("/api/v1/kb", json={"name": "库一"}).json()["data"]["id"]
        kb2 = master.post("/api/v1/kb", json={"name": "库二"}).json()["data"]["id"]
        master.post(
            f"/api/v1/kb/{kb1}/documents",
            files={"file": ("a.md", "库一的秘密内容。".encode("utf-8"), "text/markdown")},
        )
        master.post(
            f"/api/v1/kb/{kb2}/documents",
            files={"file": ("b.md", "库二的秘密内容。".encode("utf-8"), "text/markdown")},
        )
    record, raw = app.state.registry.create_api_key("受限Key", [kb1])
    yield app, kb1, kb2, raw
    app.state.registry.revoke_api_key(record.id)


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def test_restricted_key_cannot_search_other_kb(env):
    app, kb1, kb2, raw = env
    with TestClient(app) as c:
        # 越权查询 kb2：必须 403（非 404、非空结果——审计 F-13）
        resp = c.post(
            "/api/v1/search", json={"query": "秘密", "kb_id": kb2}, headers=_auth(raw)
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "acl_denied"
        # 授权查询 kb1：200
        ok = c.post(
            "/api/v1/search", json={"query": "秘密", "kb_id": kb1}, headers=_auth(raw)
        )
        assert ok.status_code == 200
        assert ok.json()["data"]


def test_restricted_key_cannot_ingest_or_delete_other_kb(env):
    app, kb1, kb2, raw = env
    with TestClient(app) as c:
        up = c.post(
            f"/api/v1/kb/{kb2}/documents",
            files={"file": ("x.md", b"content", "text/markdown")},
            headers=_auth(raw),
        )
        assert up.status_code == 403
        dele = c.delete(
            f"/api/v1/kb/{kb2}/documents/whatever", headers=_auth(raw)
        )
        assert dele.status_code == 403


def test_restricted_key_cannot_chat_on_other_kb(env):
    app, kb1, kb2, raw = env
    with TestClient(app) as c:
        resp = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "秘密"}], "rag_kb_id": kb2},
            headers=_auth(raw),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "acl_denied"


def test_kb_list_is_filtered_for_restricted_key(env):
    # 审计 ARC-005：KB 名称本身是租户信息，列表必须过滤
    app, kb1, kb2, raw = env
    with TestClient(app) as c:
        listing = c.get("/api/v1/kb", headers=_auth(raw)).json()["data"]
        ids = [k["id"] for k in listing]
        assert kb1 in ids
        assert kb2 not in ids


def test_revoked_key_rejected_immediately(env):
    # 签发新 Key → 立即吊销 → 请求 401（吊销即时生效，审计 F-02）
    app, kb1, kb2, raw = env
    record, fresh = app.state.registry.create_api_key("临时", ["*"])
    app.state.registry.revoke_api_key(record.id)
    with TestClient(app) as c:
        resp = c.get("/api/v1/kb", headers=_auth(fresh))
        assert resp.status_code == 401


def test_master_key_has_full_access(env):
    app, kb1, kb2, raw = env
    with TestClient(app) as c:
        resp = c.post(
            "/api/v1/search",
            json={"query": "秘密", "kb_id": kb2},
            headers=_auth(API_KEY),
        )
        assert resp.status_code == 200
