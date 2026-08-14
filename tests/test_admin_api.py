"""管理端 API 契约测试（web-admin-auth.md §4 全项）。"""
from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.config import Settings
from core.security.admin import hash_password
from tests.conftest import API_KEY

INITIAL_PW = "initial-pass-for-test"


def _make_app(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "qdrant",
        database_url=f"sqlite:///{tmp_path / 'r.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        api_key=API_KEY,
    )
    return create_app(settings)


def _set_initial_password(app) -> None:
    # 用已知初始密码替换随机生成的（契约测试可控性；须在 lifespan 启动后调用）
    app.state.registry.set_admin_initial_password(
        app.state.registry.get_admin_user("admin").id, hash_password(INITIAL_PW)
    )


def _login(client, password=INITIAL_PW):
    resp = client.post(
        "/admin/api/login", json={"username": "admin", "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_login_flow_and_cookie_attributes(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login(client)
        assert data["role"] == "admin"
        assert data["must_change_password"] is True
        assert data["csrf_token"]
        # Cookie 已下发且属性正确
        cookies = client.cookies
        assert "rag_admin_session" in cookies
        for cookie in cookies.jar:
            if cookie.name == "rag_admin_session":
                assert cookie.has_nonstandard_attr("HttpOnly")
                assert cookie.has_nonstandard_attr("SameSite")
        me = client.get("/admin/api/me")
        assert me.json()["data"]["username"] == "admin"


def test_forced_password_change_flow(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login(client)
        resp = client.post(
            "/admin/api/change-password",
            json={"current_password": INITIAL_PW, "new_password": "new-secure-pass-1"},
            headers={"X-CSRF-Token": data["csrf_token"]},
        )
        assert resp.status_code == 200
        # 新密码可登录、旧密码失效
        client.post("/admin/api/logout", headers={"X-CSRF-Token": data["csrf_token"]})
        data2 = _login(client, "new-secure-pass-1")
        assert data2["must_change_password"] is False


def test_csrf_token_required_for_state_change(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        _login(client)
        resp = client.post(
            "/admin/api/change-password",
            json={"current_password": INITIAL_PW, "new_password": "whatever-pass-1"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "csrf_failed"


def test_channel_isolation_rejects_api_key(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get(
            "/admin/api/me", headers={"Authorization": f"Bearer {API_KEY}"}
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "channel_isolated"


def test_readonly_role_cannot_manage_keys(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        app.state.registry.ensure_admin_user(
            "viewer", hash_password("viewer-pass"), role="readonly"
        )
        data = client.post(
            "/admin/api/login", json={"username": "viewer", "password": "viewer-pass"}
        ).json()["data"]
        assert data["role"] == "readonly"
        resp = client.post(
            "/admin/api/keys",
            json={"name": "越权签发"},
            headers={"X-CSRF-Token": data["csrf_token"]},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "admin_forbidden"
        # 只读端点可用
        assert client.get("/admin/api/kb").status_code == 200


def test_admin_can_issue_and_revoke_key(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login(client)
        issued = client.post(
            "/admin/api/keys",
            json={"name": "agent-key", "kb_acl": ["*"]},
            headers={"X-CSRF-Token": data["csrf_token"]},
        )
        assert issued.status_code == 201
        raw = issued.json()["data"]["api_key"]
        assert len(raw) >= 32  # token_urlsafe(32)
        key_id = issued.json()["data"]["id"]
        # 明文 Key 可用于 API 通道
        resp = client.get("/api/v1/kb", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 200
        # 吊销即时生效
        revoked = client.delete(
            f"/admin/api/keys/{key_id}", headers={"X-CSRF-Token": data["csrf_token"]}
        )
        assert revoked.status_code == 200
        resp = client.get("/api/v1/kb", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 401


def test_unauthenticated_admin_route_401(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/admin/api/me")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "admin_unauthorized"
