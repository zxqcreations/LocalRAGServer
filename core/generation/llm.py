"""生成层：OpenAI 兼容客户端（vLLM / Ollama / llama.cpp server 通用）与 RAG 消息组装。"""
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from core.observability.logging import get_logger

SYSTEM_PROMPT = (
    "你是知识库问答助手。只能依据提供的【上下文】回答问题，"
    "并在句末用 [n] 标注引用的上下文编号；"
    "如果上下文中没有答案，明确回答“知识库中未找到相关内容”，不得编造。"
)

_logger = get_logger("local_rag_server.llm")


@dataclass(frozen=True)
class ChatResult:
    content: str


def build_rag_messages(question: str, contexts: list[str]) -> list[dict]:
    """组装 RAG 消息：上下文编号 [1]..[n]，与系统提示的引用格式对应。"""
    ctx_lines = [f"[{i}] {text}" for i, text in enumerate(contexts, start=1)]
    context_block = "\n\n".join(ctx_lines) if ctx_lines else "（无相关上下文）"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"【上下文】\n{context_block}\n\n【问题】\n{question}"},
    ]


class ChatClient:
    """OpenAI 兼容聊天客户端，支持流式与非流式。"""

    def __init__(
        self, base_url: str, api_key: str = "", model: str = "", timeout: float = 120.0
    ) -> None:
        # 归一化 base_url：兼容带/不带 /v1 前缀的写法，统一按 /v1/chat/completions 请求
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v1"):
            normalized = normalized[: -len("/v1")]
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # trust_env=False：目标为本地推理服务，绝不走系统代理（Windows 注册表代理会劫持回环请求）
        self._client = httpx.Client(
            base_url=normalized, headers=headers, timeout=timeout, trust_env=False
        )
        self._model = model

    def chat(self, messages: list[dict]) -> ChatResult:
        # structlog-integration.md D2：LLM 调用事件（消息体不落日志）
        started = time.perf_counter()
        resp = self._client.post(
            "/v1/chat/completions",
            json={"model": self._model, "messages": messages, "stream": False},
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"] or ""
        _logger.info(
            "llm_call", model=self._model, duration_ms=(time.perf_counter() - started) * 1000
        )
        return ChatResult(content=content)

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        """逐 token 产出内容（SSE）。"""
        started = time.perf_counter()
        with self._client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": self._model, "messages": messages, "stream": True},
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                piece = (chunk["choices"][0].get("delta") or {}).get("content")
                if piece:
                    yield piece
        _logger.info(
            "llm_call", model=self._model, duration_ms=(time.perf_counter() - started) * 1000
        )

    @property
    def client(self) -> httpx.Client:
        """底层 httpx 客户端（探针连通性检测用）。"""
        return self._client

    def close(self) -> None:
        self._client.close()
