"""API 集成测试：真实 Qdrant 本地模式 + SQLite + stub 嵌入 + 假 LLM 服务。

共享 client fixture 定义于 conftest.py。
"""
import json

from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.config import Settings


def _md_file(name="note.md", content="# 标题\n\n这是文档正文。量子计算使用量子比特。"):
    return {"file": (name, content.encode("utf-8"), "text/markdown")}


def _create_kb(client) -> str:
    return client.post("/api/v1/kb", json={"name": "库"}).json()["data"]["id"]


def _set_llm_response(server, content: str):
    handler = server.RequestHandlerClass
    handler.response_status = 200
    handler.response_body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    ).encode("utf-8")


# ---------- 运维 ----------


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_trace_id_and_metrics(client):
    # observability.md §2：X-Trace-Id 响应头 + 错误率计数
    resp = client.get("/health")
    assert "X-Trace-Id" not in resp.headers  # 非 API 路径不追踪
    kb_id = _create_kb(client)
    search = client.post("/api/v1/search", json={"query": "量子", "kb_id": kb_id})
    assert "X-Trace-Id" in search.headers
    client.post("/api/v1/search", json={"query": "x", "kb_id": "nope"})
    metrics = client.app.state.metrics.snapshot()
    assert metrics["counters"].get("api.requests", 0) >= 2
    assert metrics["counters"].get("api.errors", 0) >= 1
    assert "search.latency_ms" in metrics["latencies"]


def test_healthz_and_readyz(client):
    # 探针免认证（审计 ARC-010）
    hz = client.get("/healthz", headers={})
    assert hz.status_code == 200
    rz = client.get("/readyz", headers={})
    assert rz.status_code == 200
    checks = rz.json()["data"]["checks"]
    assert checks["database"] == "ok"
    assert checks["qdrant"] == "ok"
    assert "embedder" in checks


# ---------- 知识库 ----------


def test_create_list_get_kb(client):
    resp = client.post("/api/v1/kb", json={"name": "测试库", "kb_type": "document"})
    assert resp.status_code == 201
    kb_id = resp.json()["data"]["id"]

    listing = client.get("/api/v1/kb").json()
    assert listing["success"] and len(listing["data"]) == 1

    detail = client.get(f"/api/v1/kb/{kb_id}").json()
    assert detail["data"]["name"] == "测试库"

    missing = client.get("/api/v1/kb/nonexistent")
    assert missing.status_code == 404
    assert missing.json()["success"] is False


def test_create_kb_rejects_bad_type(client):
    resp = client.post("/api/v1/kb", json={"name": "x", "kb_type": "image"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# ---------- 文档 ----------


def test_upload_search_delete_flow(client):
    kb_id = _create_kb(client)
    up = client.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file())
    assert up.status_code == 201
    doc = up.json()["data"]
    assert doc["status"] == "ready"
    assert doc["chunk_count"] >= 1

    search = client.post(
        "/api/v1/search", json={"query": "量子比特", "kb_id": kb_id, "top_k": 3}
    ).json()
    assert search["success"] and search["data"]

    dele = client.delete(f"/api/v1/kb/{kb_id}/documents/{doc['id']}")
    assert dele.status_code == 200
    after = client.post("/api/v1/search", json={"query": "量子比特", "kb_id": kb_id}).json()
    assert after["data"] == []


def test_upload_rejects_unsupported_format(client):
    kb_id = _create_kb(client)
    resp = client.post(
        f"/api/v1/kb/{kb_id}/documents",
        files={"file": ("bad.docx", b"x", "application/octet-stream")},
    )
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "unsupported_format"


def test_upload_rejects_content_mismatching_extension(client):
    # 审计 F-07：扩展名与文件内容魔数不符 → 拒绝（MIME 魔数校验，不信任扩展名）
    kb_id = _create_kb(client)
    resp = client.post(
        f"/api/v1/kb/{kb_id}/documents",
        files={"file": ("fake.pdf", b"this is not a pdf", "application/pdf")},
    )
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "invalid_file_content"


def test_upload_empty_document_422(client):
    kb_id = _create_kb(client)
    resp = client.post(
        f"/api/v1/kb/{kb_id}/documents",
        files={"file": ("empty.md", b"   ", "text/markdown")},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "empty_document"


def test_upload_duplicate_is_idempotent(client):
    kb_id = _create_kb(client)
    first = client.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file()).json()
    second = client.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file()).json()
    assert first["data"]["id"] == second["data"]["id"]
    listing = client.get(f"/api/v1/kb/{kb_id}/documents").json()
    assert listing["meta"]["total"] == 1


def test_document_list_and_detail(client):
    kb_id = _create_kb(client)
    doc = client.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file()).json()["data"]
    listing = client.get(f"/api/v1/kb/{kb_id}/documents").json()
    assert listing["success"] and listing["meta"]["total"] == 1
    detail = client.get(f"/api/v1/kb/{kb_id}/documents/{doc['id']}").json()
    assert detail["data"]["title"] == "note.md"
    assert detail["data"]["status"] == "ready"
    missing = client.get(f"/api/v1/kb/{kb_id}/documents/nonexistent")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "doc_not_found"


# ---------- 检索 ----------


def test_search_unknown_kb_404(client):
    resp = client.post("/api/v1/search", json={"query": "x", "kb_id": "nope"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "kb_not_found"


# ---------- URL 摄取 + SSRF ----------


def test_ingest_url_ssrf_blocked(client, fake_site):
    # 默认 allow_loopback=False：本机地址被 SSRF 防护拦截（审计 F-10）
    kb_id = _create_kb(client)
    resp = client.post(f"/api/v1/kb/{kb_id}/documents/url", json={"url": fake_site.url})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ssrf_blocked"


def test_ingest_url_endpoint(tmp_path, fake_site, auth_headers, monkeypatch):
    from core.ingest import tasks as tasks_mod
    from core.ingest.pipeline import IngestPipeline

    settings = Settings(
        data_dir=tmp_path / "data3",
        qdrant_path=tmp_path / "qdrant3",
        database_url=f"sqlite:///{tmp_path / 'r3.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        api_key=auth_headers["Authorization"].removeprefix("Bearer "),
        url_fetch_allow_loopback=True,  # 测试开关：本机假网站
    )
    app = create_app(settings)
    with TestClient(app, headers=auth_headers) as c:
        # 用应用自身服务构建共享管线（同一 Qdrant 客户端，避免本地模式目录锁冲突）
        pipeline = IngestPipeline(
            app.state.registry,
            app.state.search_service,
            settings.data_dir / "ingest_work",
        )
        monkeypatch.setattr(tasks_mod, "_pipeline", lambda: pipeline)
        tasks_mod.app.conf.task_always_eager = True
        tasks_mod.app.conf.task_eager_propagates = True

        kb_id = c.post("/api/v1/kb", json={"name": "web库"}).json()["data"]["id"]
        resp = c.post(f"/api/v1/kb/{kb_id}/documents/url", json={"url": fake_site.url})
        assert resp.status_code == 202
        doc_id = resp.json()["data"]["doc_id"]
        # eager 模式下任务链已执行完毕
        assert app.state.registry.get_document(kb_id, doc_id).status == "ready"
        hits = c.post(
            "/api/v1/search", json={"query": "正文内容", "kb_id": kb_id, "top_k": 3}
        ).json()["data"]
        assert hits and hits[0]["doc_title"] == "测试"


# ---------- Chat ----------


def test_chat_with_rag_returns_content_and_citations(client, fake_llm_server):
    _set_llm_response(fake_llm_server, "量子比特是量子计算的基本单元。[1]")
    kb_id = _create_kb(client)
    client.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file())

    resp = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "什么是量子比特？"}],
            "rag_kb_id": kb_id,
            "rag_top_k": 3,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "量子比特是量子计算的基本单元。[1]"
    citations = body["choices"][0]["message"]["citations"]
    # 上传文档标题保留原始文件名（含扩展名）
    assert citations and citations[0]["doc_title"] == "note.md"


def test_chat_refuses_without_calling_llm(client, fake_llm_server, tmp_path, auth_headers, capsys):
    # 阈值调高 → 命中分数不足 → 拒答且不调用 LLM
    settings = Settings(
        data_dir=tmp_path / "data2",
        qdrant_path=tmp_path / "qdrant2",
        database_url=f"sqlite:///{tmp_path / 'registry2.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        llm_base_url=f"http://127.0.0.1:{fake_llm_server.server_port}/v1",
        llm_model="qwen-test",
        refusal_threshold=1.0,
        api_key=auth_headers["Authorization"].removeprefix("Bearer "),
    )
    app = create_app(settings)
    with TestClient(app, headers=auth_headers) as strict:
        _set_llm_response(fake_llm_server, "不应出现的回答")
        kb_id = strict.post("/api/v1/kb", json={"name": "库"}).json()["data"]["id"]
        strict.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file())
        resp = strict.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "什么是量子比特？"}],
                "rag_kb_id": kb_id,
            },
        )
        assert resp.json()["choices"][0]["message"]["content"] == "知识库中未找到相关内容。"
        assert fake_llm_server.RequestHandlerClass.calls == 0
        # 审查 M2(b)：拒答事件（结构化 + kb_id 关联）
        refused = _json_events(capsys.readouterr().out, "search_refused")
        assert refused and refused[-1]["kb_id"] == kb_id
        assert refused[-1]["detail"] == "refused"


def test_chat_passthrough_without_rag(client, fake_llm_server):
    _set_llm_response(fake_llm_server, "纯透传回答")
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "你好"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "纯透传回答"


def test_chat_with_rag_streams(client, fake_llm_server):
    body = (
        'data: {"choices": [{"delta": {"content": "流式"}}]}\n\n'
        'data: {"choices": [{"delta": {"content": "回答"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    handler = fake_llm_server.RequestHandlerClass
    handler.response_status = 200
    handler.response_body = body.encode("utf-8")
    kb_id = _create_kb(client)
    client.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file())

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "量子比特"}],
            "rag_kb_id": kb_id,
            "stream": True,
        },
    ) as resp:
        assert resp.status_code == 200
        content = "".join(
            json.loads(line[len("data:") :])["choices"][0]["delta"]["content"]
            for line in resp.iter_lines()
            if line.startswith("data:") and line != "data: [DONE]"
        )
    assert content == "流式回答"


def test_chat_stream_generator_close_emits_aborted_event(fake_llm_server, capsys):
    # 审查 H2：消费者提前关闭生成器（客户端断连的确定性等价）时
    # llm_call 事件照发且 aborted=true（HTTP 层断连为竞态路径，实测由审查完成）
    from core.generation.llm import ChatClient

    body = (
        'data: {"choices": [{"delta": {"content": "流"}}]}\n\n'
        'data: {"choices": [{"delta": {"content": "式"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    handler = fake_llm_server.RequestHandlerClass
    handler.response_status = 200
    handler.response_body = body.encode("utf-8")

    chat = ChatClient(
        f"http://127.0.0.1:{fake_llm_server.server_port}/v1", api_key="", model="m"
    )
    gen = chat.chat_stream([{"role": "user", "content": "hi"}])
    assert next(gen) == "流"
    gen.close()  # 确定性 GeneratorExit（等价客户端断连）
    events = _json_events(capsys.readouterr().out, "llm_call")
    assert events, "关闭路径未发 llm_call 事件"
    assert events[-1]["aborted"] is True


def test_chat_unknown_kb_404(client):
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "x"}], "rag_kb_id": "nope"},
    )
    assert resp.status_code == 404


# ---------- 审查回归：trace 最外层 + 限流事件（M1/M2a） ----------


def test_json_body_size_limit(client):
    # 安全审计 M-5：Content-Length 超限 → 413（防大 body 打内存）
    big = "x" * (11 * 1024 * 1024)
    resp = client.post(
        "/api/v1/search",
        json={"query": big, "kb_id": "k"},
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "body_too_large"


def test_auth_rejection_carries_trace_id(client):
    # 审查 M1：trace 中间件在最外层，认证拒绝路径同样带 X-Trace-Id
    resp = client.get("/api/v1/kb", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401
    assert "X-Trace-Id" in resp.headers


def test_rate_limited_emits_event_with_trace(client, capsys):
    # 审查 M2(a)：429 限流事件带 trace_id（滥用检测信号需关联被拒请求）
    for _ in range(30):  # per-IP 桶 30/0.5s，耗尽
        client.get("/api/v1/kb")
    resp = client.get("/api/v1/kb")
    assert resp.status_code == 429
    out = capsys.readouterr().out
    events = _json_events(out, "rate_limited")
    assert events, "未捕获到 rate_limited 事件"
    event = events[-1]
    assert event["trace_id"] == resp.headers["X-Trace-Id"]
    assert event["actor"].startswith("ip:")
    assert "limit" in event


# ---------- 结构化日志（structlog-integration.md D1/D2） ----------


def _json_events(out: str, event_name: str) -> list[dict]:
    """从捕获输出解析指定事件名的 structlog JSON 行。"""
    events = []
    for line in out.splitlines():
        try:
            payload = json.loads(line)
        except (ValueError, TypeError):
            continue
        if payload.get("event") == event_name:
            events.append(payload)
    return events


def test_audit_records_carry_trace_id(client):
    # 安全审计 M-2：审计条目与结构化日志同 trace 关联
    kb_id = _create_kb(client)
    client.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file())
    resp = client.post("/api/v1/search", json={"query": "量子", "kb_id": kb_id})
    trace_id = resp.headers["X-Trace-Id"]
    entries = client.app.state.registry.list_audit(limit=5)
    assert any(e.trace_id == trace_id for e in entries)


def test_search_emits_traced_event_without_query_text(client, capsys):
    # D1/D2：检索事件携带 trace_id（与 X-Trace-Id 一致）；查询文本不落日志
    kb_id = _create_kb(client)
    client.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file())
    resp = client.post("/api/v1/search", json={"query": "量子比特", "kb_id": kb_id})
    trace_id = resp.headers["X-Trace-Id"]
    out = capsys.readouterr().out  # 一次性捕获（审查 M3：二次调用返回空串，断言恒真）
    events = _json_events(out, "search_ok")
    assert events, "未捕获到 search_ok 结构化事件"
    event = events[-1]
    assert event["trace_id"] == trace_id
    assert event["kb_id"] == kb_id
    assert event["hits"] >= 1
    assert isinstance(event["duration_ms"], (int, float))
    assert "量子" not in out  # 查询文本不落日志


def test_chat_emits_llm_call_event(client, fake_llm_server, capsys):
    _set_llm_response(fake_llm_server, "量子比特是基本单元。[1]")
    kb_id = _create_kb(client)
    client.post(f"/api/v1/kb/{kb_id}/documents", files=_md_file())
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "什么是量子比特？"}], "rag_kb_id": kb_id},
    )
    assert resp.status_code == 200
    out = capsys.readouterr().out  # 一次性捕获（审查 M3）
    events = _json_events(out, "llm_call")
    assert events, "未捕获到 llm_call 结构化事件"
    event = events[-1]
    assert "model" in event and "duration_ms" in event
    # 消息体不落日志（D2 约束）
    assert "什么是量子比特" not in out
