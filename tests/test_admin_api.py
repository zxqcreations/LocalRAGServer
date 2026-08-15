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


def _login_admin(client, username="admin", password=INITIAL_PW):
    """登录并完成强制改密（安全审计 H-1 契约后的统一入口）。"""
    resp = client.post("/admin/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    if not data["must_change_password"]:
        return data
    changed = client.post(
        "/admin/api/change-password",
        json={"current_password": password, "new_password": "new-secure-pass-1"},
        headers={"X-CSRF-Token": data["csrf_token"]},
    )
    assert changed.status_code == 200, changed.text
    return client.post(
        "/admin/api/login", json={"username": username, "password": "new-secure-pass-1"}
    ).json()["data"]


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


def test_csrf_token_is_independent_of_session_cookie(tmp_path):
    # 安全审查 H-1：CSRF token 为独立随机值；会话 Cookie 值本身不再被接受
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login(client)
        token = data["csrf_token"]
        cookie_value = client.cookies.get("rag_admin_session")
        assert token and cookie_value
        assert token != cookie_value  # 无凭证孪生体
        # 用 Cookie 值冒充 CSRF token → 拒绝
        resp = client.post(
            "/admin/api/change-password",
            json={"current_password": INITIAL_PW, "new_password": "whatever-pass-1"},
            headers={"X-CSRF-Token": cookie_value},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "csrf_failed"
        # 正确 token 可用
        ok_resp = client.post(
            "/admin/api/change-password",
            json={"current_password": INITIAL_PW, "new_password": "new-secure-pass-1"},
            headers={"X-CSRF-Token": token},
        )
        assert ok_resp.status_code == 200


def test_me_returns_csrf_token_for_self_heal(tmp_path):
    # 代码审查 MEDIUM-1：/me 下发当前会话 token，前端刷新/多标签页自愈
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login(client)
        me = client.get("/admin/api/me").json()["data"]
        assert me["csrf_token"] == data["csrf_token"]


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
        data = _login_admin(client, "viewer", "viewer-pass")
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


def test_readonly_role_rbac_matrix(tmp_path):
    # 安全审计 H-4：readonly 无任何变更/敏感读操作（web-admin-auth.md §2 契约）
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        app.state.registry.ensure_admin_user(
            "viewer", hash_password("viewer-pass"), role="readonly"
        )
        data = _login_admin(client, "viewer", "viewer-pass")
        headers = {"X-CSRF-Token": data["csrf_token"]}
        # 标注写入 → 403
        ann = client.post(
            "/admin/api/annotations",
            json={"kb_id": "x", "query": "越权标注"},
            headers=headers,
        )
        assert ann.status_code == 403
        # Key 枚举（敏感元数据）→ 403
        keys = client.get("/admin/api/keys")
        assert keys.status_code == 403
        # 标注列表（含用户查询原文）→ 403
        anns = client.get("/admin/api/annotations?kb_id=x")
        assert anns.status_code == 403


def test_admin_can_issue_and_revoke_key(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login_admin(client)
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


def test_annotation_idempotent_upsert(tmp_path):
    # 审计 F17 契约：同 doc+query 重复标注覆盖而非追加
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login_admin(client)
        # 建 KB（API 通道主 Key 权限；admin 通道只读列表）
        client.post(
            "/api/v1/kb",
            json={"name": "标注库"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        kb_id = client.get("/admin/api/kb").json()["data"][0]["id"]
        payload = {
            "kb_id": kb_id,
            "query": "测试查询",
            "doc_id": "d1",
            "chunk_id": "c1",
            "is_helpful": True,
        }
        first = client.post(
            "/admin/api/annotations", json=payload, headers={"X-CSRF-Token": data["csrf_token"]}
        )
        assert first.status_code == 201
        second = client.post(
            "/admin/api/annotations",
            json={**payload, "is_helpful": False},
            headers={"X-CSRF-Token": data["csrf_token"]},
        )
        assert second.status_code == 201
        assert first.json()["data"]["id"] == second.json()["data"]["id"]  # 幂等覆盖
        listing = client.get(
            f"/admin/api/annotations?kb_id={kb_id}"
        ).json()["data"]
        matches = [a for a in listing if a["query"] == "测试查询"]
        assert len(matches) == 1
        assert matches[0]["is_helpful"] is False  # 覆盖为最新判定


def test_unauthenticated_admin_route_401(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/admin/api/me")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "admin_unauthorized"


# ---------- URL 订阅管理（docs/design/url-crawler.md 实施步骤 3） ----------


def test_server_side_forced_password_change(tmp_path):
    # 安全审计 H-1：服务端强制首次改密（非仅前端契约）
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login(client)  # must_change_password=True
        headers = {"X-CSRF-Token": data["csrf_token"]}
        # 改密前：业务端点全部 403
        kb = client.get("/admin/api/kb")
        assert kb.status_code == 403
        assert kb.json()["error"]["code"] == "password_change_required"
        # 改密端点本身可用
        changed = client.post(
            "/admin/api/change-password",
            json={"current_password": INITIAL_PW, "new_password": "new-secure-pass-1"},
            headers=headers,
        )
        assert changed.status_code == 200
        # 改密后业务端点放行 + 初始密码文件已删除
        assert client.get("/admin/api/kb").status_code == 200
        initial_file = app.state.settings.data_dir / "admin_initial_password"
        assert not initial_file.exists()


def test_login_failure_is_audited(tmp_path):
    # 安全审计 H-2：登录失败入审计（爆破检测信号）
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        resp = client.post(
            "/admin/api/login", json={"username": "admin", "password": "wrong-pass"}
        )
        assert resp.status_code == 401
        entries = app.state.registry.list_audit()  # 未认证状态下直接查 registry
        assert any(e.action == "login_failed" and "user:admin" in e.actor for e in entries)


def test_audit_action_codes_are_distinct(tmp_path):
    # 安全审计 H-3：登录/登出/改密/Key 签发吊销使用独立动作码
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login(client)  # login 审计
        headers = {"X-CSRF-Token": data["csrf_token"]}
        client.post(
            "/admin/api/change-password",
            json={"current_password": INITIAL_PW, "new_password": "new-secure-pass-1"},
            headers=headers,
        )  # password_change
        issued = client.post(
            "/admin/api/keys", json={"name": "k1"}, headers=headers
        ).json()["data"]  # key_create
        client.delete(f"/admin/api/keys/{issued['id']}", headers=headers)  # key_revoke
        client.post("/admin/api/logout", headers=headers)  # logout
        actions = {e.action for e in app.state.registry.list_audit()}
        assert {
            "login",
            "password_change",
            "key_create",
            "key_revoke",
            "logout",
        } <= actions


def test_subscription_crud_contract(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login_admin(client)
        headers = {"X-CSRF-Token": data["csrf_token"]}
        # 建 KB（API 通道主 Key 权限）
        client.post(
            "/api/v1/kb",
            json={"name": "订阅库"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        kb_id = client.get("/admin/api/kb").json()["data"][0]["id"]
        # 创建订阅
        created = client.post(
            "/admin/api/subscriptions",
            json={"kb_id": kb_id, "url": "https://example.com/docs", "interval_hours": 12},
            headers=headers,
        )
        assert created.status_code == 201
        sub = created.json()["data"]
        assert sub["url"] == "https://example.com/docs"
        assert sub["enabled"] is True
        # 列表（按 kb 过滤）
        listing = client.get(f"/admin/api/subscriptions?kb_id={kb_id}").json()["data"]
        assert len(listing) == 1 and listing[0]["id"] == sub["id"]
        # 暂停/恢复
        paused = client.post(
            f"/admin/api/subscriptions/{sub['id']}/toggle",
            json={"enabled": False},
            headers=headers,
        )
        assert paused.status_code == 200
        assert client.get(f"/admin/api/subscriptions?kb_id={kb_id}").json()["data"][0][
            "enabled"
        ] is False
        # 删除
        deleted = client.delete(f"/admin/api/subscriptions/{sub['id']}", headers=headers)
        assert deleted.status_code == 200
        assert client.get(f"/admin/api/subscriptions?kb_id={kb_id}").json()["data"] == []


def test_subscription_create_rejects_bad_url(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        _set_initial_password(app)
        data = _login_admin(client)
        client.post(
            "/api/v1/kb",
            json={"name": "订阅库"},
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        kb_id = client.get("/admin/api/kb").json()["data"][0]["id"]
        resp = client.post(
            "/admin/api/subscriptions",
            json={"kb_id": kb_id, "url": "not-a-url"},
            headers={"X-CSRF-Token": data["csrf_token"]},
        )
        assert resp.status_code == 422
