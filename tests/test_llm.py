"""LLM 客户端单测：RAG 消息组装 + 假 LLM 服务（conftest 提供，零网络依赖）。"""
import json

import httpx
import pytest

from core.generation.llm import ChatClient, build_rag_messages


def _set_response(server, content: str, status: int = 200):
    handler = server.RequestHandlerClass
    handler.response_status = status
    handler.response_body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    ).encode("utf-8")


def test_build_rag_messages_numbers_contexts():
    messages = build_rag_messages("什么是量子比特？", ["上下文甲", "上下文乙"])
    assert messages[0]["role"] == "system"
    assert "不得编造" in messages[0]["content"]
    user = messages[1]["content"]
    assert "[1] 上下文甲" in user
    assert "[2] 上下文乙" in user
    assert "什么是量子比特？" in user


def test_build_rag_messages_empty_contexts():
    messages = build_rag_messages("问题", [])
    assert "（无相关上下文）" in messages[1]["content"]


def test_chat_parses_openai_response(fake_llm_server):
    _set_response(fake_llm_server, "答案是量子态。")
    client = ChatClient(
        base_url=f"http://127.0.0.1:{fake_llm_server.server_port}/v1", model="qwen"
    )
    result = client.chat([{"role": "user", "content": "你好"}])
    assert result.content == "答案是量子态。"
    client.close()


def test_chat_base_url_without_v1_prefix(fake_llm_server):
    # base_url 不带 /v1 时同样能请求到 /v1/chat/completions
    _set_response(fake_llm_server, "ok")
    client = ChatClient(
        base_url=f"http://127.0.0.1:{fake_llm_server.server_port}", model="qwen"
    )
    result = client.chat([{"role": "user", "content": "你好"}])
    assert result.content == "ok"
    client.close()


def test_chat_raises_on_http_error(fake_llm_server):
    _set_response(fake_llm_server, "", status=500)
    client = ChatClient(
        base_url=f"http://127.0.0.1:{fake_llm_server.server_port}/v1",
        model="qwen",
        timeout=10.0,
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.chat([{"role": "user", "content": "你好"}])
    client.close()


def test_chat_stream_yields_tokens(fake_llm_server):
    body = (
        'data: {"choices": [{"delta": {"content": "你好"}}]}\n\n'
        'data: {"choices": [{"delta": {"content": "，世界"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    handler = fake_llm_server.RequestHandlerClass
    handler.response_status = 200
    handler.response_body = body.encode("utf-8")
    client = ChatClient(
        base_url=f"http://127.0.0.1:{fake_llm_server.server_port}/v1", model="qwen"
    )
    pieces = list(client.chat_stream([{"role": "user", "content": "你好"}]))
    assert "".join(pieces) == "你好，世界"
    client.close()
