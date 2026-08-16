"""MCP streamable HTTP transport（ADR-006 v1.1）越权矩阵测试。

经真实 HTTP 通道（/mcp，FastAPI 挂载）验证 ADR-006 前置条件：
- 认证强制（无 Bearer → 401，与 REST 同一强制点）
- 请求级 ACL 注入（跨库检索/任务状态越权 → 工具级显式错误）
- allow_local_paths=False（远程通道拒绝本地路径摄取，审计 H-2）
- 受限 Key 列表过滤（仅显授权 KB）
- 工具调用审计落库（mcp_* 动作码 + actor + trace_id，含 M-7 job 归属校验）
"""
import pytest
from conftest import API_KEY  # pytest 以顶层模块名加载（勿改 tests.conftest——双重加载根源）


def _rpc(method: str, params: dict, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params, "id": id_}


def _init_payload() -> dict:
    return {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0.1"},
    }


def _init_session(c, headers: dict) -> str:
    resp = c.post("/mcp", json=_rpc("initialize", _init_payload()), headers=headers)
    assert resp.status_code == 200, resp.text
    sid = resp.headers.get("Mcp-Session-Id")
    assert sid, "initialize 未返回 Mcp-Session-Id"
    return sid


def _call_tool(c, session_id: str, headers: dict, name: str, arguments: dict) -> dict:
    resp = c.post(
        "/mcp",
        json=_rpc("tools/call", {"name": name, "arguments": arguments}, id_=2),
        headers={**headers, "Mcp-Session-Id": session_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def _is_error(result: dict) -> bool:
    return bool(result.get("isError"))


def _text(result: dict) -> str:
    return result["content"][0]["text"]


@pytest.fixture
def mcp_env(client, tmp_path):
    """两个 KB（授权/隔离）+ 一篇已摄取文档 + 一个受限 Key（仅授权库）。"""
    registry = client.app.state.registry
    search_service = client.app.state.search_service
    kb_a = registry.create_kb("授权库")
    kb_b = registry.create_kb("隔离库")
    src = tmp_path / "a.md"
    src.write_text("# 文档\n\n量子计算使用量子比特与叠加态。", encoding="utf-8")
    search_service.ingest_file(kb_a.id, src)
    # 隔离库中的一个摄取任务（供 M-7 跨库探测）
    doc_b = registry.create_document(kb_b.id, "b.md", "local://b.md", "hash-b")
    job_b = registry.create_job(doc_b.id, kb_b.id)
    # 授权库自身的任务（M-7 授权路径）
    doc_a = registry.create_document(kb_a.id, "a2.md", "local://a2.md", "hash-a")
    job_a = registry.create_job(doc_a.id, kb_a.id)
    record, raw = registry.create_api_key("受限key", [kb_a.id])
    return {
        "registry": registry,
        "job_a": job_a,
        "job_b": job_b,
        "key_record": record,
        "master_headers": {"Authorization": f"Bearer {API_KEY}"},
        "restricted_headers": {"Authorization": f"Bearer {raw}"},
    }


def test_mcp_requires_bearer_token(client):
    resp = client.post(
        "/mcp", json=_rpc("initialize", _init_payload()),
        headers={"Authorization": ""},
    )
    assert resp.status_code == 401
    assert resp.json()["success"] is False


def test_master_key_full_flow_and_audit(mcp_env, client):
    sid = _init_session(client, mcp_env["master_headers"])
    result = _call_tool(
        client, sid, mcp_env["master_headers"], "search_knowledge",
        {"query": "量子比特", "kb": "授权库"},
    )
    assert not _is_error(result)
    assert "量子" in _text(result)
    # 审计：master 主体 + trace_id 非空（trace 中间件已覆盖 /mcp）
    audit = [
        e for e in mcp_env["registry"].list_audit(limit=20) if e.action == "mcp_search"
    ]
    assert audit, "MCP 工具调用未落审计"
    assert audit[0].actor == "master"
    assert audit[0].trace_id


def test_restricted_key_privilege_matrix(mcp_env, client):
    env = mcp_env
    headers = env["restricted_headers"]
    sid = _init_session(client, headers)

    # 1) 跨库检索 → 显式拒绝（与 REST 403 同语义）
    denied = _call_tool(
        client, sid, headers, "search_knowledge", {"query": "x", "kb": "隔离库"}
    )
    assert _is_error(denied)
    assert "无权限" in _text(denied)

    # 2) 授权库检索 → 正常
    ok = _call_tool(
        client, sid, headers, "search_knowledge", {"query": "量子", "kb": "授权库"}
    )
    assert not _is_error(ok)
    assert "量子" in _text(ok)

    # 3) M-7：跨库任务状态探测 → 显式拒绝
    denied_job = _call_tool(
        client, sid, headers, "get_document_status", {"job_id": env["job_b"].id}
    )
    assert _is_error(denied_job)
    assert "无权限" in _text(denied_job)

    # 4) M-7 授权路径：本库任务 → 正常
    own_job = _call_tool(
        client, sid, headers, "get_document_status", {"job_id": env["job_a"].id}
    )
    assert not _is_error(own_job)
    assert "job=" in _text(own_job)

    # 5) H-2：远程通道拒绝本地路径摄取
    denied_ingest = _call_tool(
        client, sid, headers, "ingest_document", {"path": __file__, "kb": "授权库"}
    )
    assert _is_error(denied_ingest)
    assert "不支持本地文件摄取" in _text(denied_ingest)

    # 6) 列表过滤：仅显授权 KB
    listed = _call_tool(client, sid, headers, "list_knowledge_bases", {})
    text = _text(listed)
    assert "授权库" in text
    assert "隔离库" not in text

    # 7) 审计主体 = Key id，trace_id 全量非空；越权拒绝落独立 mcp_*_denied 动作码
    audit = [
        e for e in env["registry"].list_audit(limit=50) if e.action.startswith("mcp_")
    ]
    assert audit
    assert all(e.actor == env["key_record"].id for e in audit)
    assert all(e.trace_id for e in audit)
    denied_actions = {e.action for e in audit}
    assert "mcp_search_knowledge_denied" in denied_actions
    assert "mcp_get_document_status_denied" in denied_actions


def test_cross_credential_session_resume(mcp_env, client):
    """安全审查 H-1：SDK 会话归属绑定——跨 Key 恢复会话与「会话不存在」同响应。"""
    env = mcp_env
    # master 建立会话，受限 Key 恢复 → 404（不得以他人会话注入消息）
    sid_master = _init_session(client, env["master_headers"])
    resp = client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_knowledge_bases", "arguments": {}}, id_=3),
        headers={**env["restricted_headers"], "Mcp-Session-Id": sid_master},
    )
    assert resp.status_code == 404
    assert "Session not found" in resp.text
    # 反向：master 恢复受限 Key 的会话同样被拒
    sid_restricted = _init_session(client, env["restricted_headers"])
    resp2 = client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_knowledge_bases", "arguments": {}}, id_=4),
        headers={**env["master_headers"], "Mcp-Session-Id": sid_restricted},
    )
    assert resp2.status_code == 404


def test_session_termination_and_unknown_session(mcp_env, client):
    env = mcp_env
    sid = _init_session(client, env["master_headers"])
    # 未知会话 → 404（与真实会话不存在不可区分）
    resp = client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_knowledge_bases", "arguments": {}}, id_=2),
        headers={**env["master_headers"], "Mcp-Session-Id": "deadbeef"},
    )
    assert resp.status_code == 404
    # DELETE 终止会话，终止后再调用 → 404
    resp_del = client.delete(
        "/mcp", headers={**env["master_headers"], "Mcp-Session-Id": sid}
    )
    assert resp_del.status_code in (200, 202, 204)
    resp2 = client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_knowledge_bases", "arguments": {}}, id_=3),
        headers={**env["master_headers"], "Mcp-Session-Id": sid},
    )
    assert resp2.status_code == 404


def test_chunked_body_limit_413(mcp_env, client):
    """安全审查 M：chunked 请求无 Content-Length，ASGI 层按实际字节数钳制。"""
    env = mcp_env

    def _chunks():
        yield b"x" * (5 * 1024 * 1024)
        yield b"x" * (5 * 1024 * 1024)
        yield b"x" * 1024

    resp = client.post("/mcp", data=_chunks(), headers=env["master_headers"])
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "body_too_large"
