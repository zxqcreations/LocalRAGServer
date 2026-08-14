"""Chat 路由：OpenAI 兼容 /v1/chat/completions。

扩展字段：rag_kb_id 指定知识库时执行「检索 → 拒答判定 → RAG 生成」；
为空时纯 LLM 透传。返回消息携带 citations 扩展字段。
"""
import json
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from apps.api.deps import (
    get_allowed_kbs,
    get_chat_client,
    get_registry,
    get_search_service,
    get_settings,
)
from apps.api.errors import KB_NOT_FOUND, raise_http
from apps.api.schemas import (
    ChatChoice,
    ChatChoiceMessage,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Citation,
)
from core.config import Settings
from core.generation.llm import ChatClient, build_rag_messages
from core.generation.refusal import refusal_field, should_refuse
from core.retrieval.search import SearchService
from core.security.acl import AllowedKbs, require_kb_access
from core.storage.registry import Registry

router = APIRouter()

REFUSAL_TEXT = "知识库中未找到相关内容。"

RegistryDep = Annotated[Registry, Depends(get_registry)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
ChatClientDep = Annotated[ChatClient, Depends(get_chat_client)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
AllowedKbsDep = Annotated[AllowedKbs, Depends(get_allowed_kbs)]


def _citations(results) -> list[Citation]:
    return [
        Citation(
            chunk_id=r.chunk_id,
            doc_id=r.doc_id,
            doc_title=r.doc_title,
            score=round(r.score, 4),
        )
        for r in results
    ]


def _stream_response(chat_client: ChatClient, model: str, messages: list[dict]):
    def gen():
        for piece in chat_client.chat_stream(messages):
            chunk = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/chat/completions", response_model=ChatResponse)
def chat_completions(
    body: ChatRequest,
    registry: RegistryDep,
    search_service: SearchServiceDep,
    chat_client: ChatClientDep,
    settings: SettingsDep,
    allowed: AllowedKbsDep,
):
    model = body.model or settings.llm_model
    question = body.messages[-1].content if body.messages[-1].role == "user" else ""
    citations: list[Citation] = []

    if body.rag_kb_id is not None:
        require_kb_access(body.rag_kb_id, allowed)  # ACL 强制点（生成链路）
        if registry.get_kb(body.rag_kb_id) is None:
            raise_http(404, KB_NOT_FOUND, "知识库不存在")
        results = search_service.search(body.rag_kb_id, question, body.rag_top_k)
        # 拒答判定（审计 F12/ARC-014）：字段按重排是否激活选择，阈值语义 best < threshold
        field = refusal_field(rerank_active=settings.rerank_backend != "off")
        if should_refuse(results, settings.refusal_threshold, field=field):
            content = REFUSAL_TEXT
        else:
            # 生成用 parent 回填后的上下文（架构 §5：子块检索、父块入生成）
            messages = build_rag_messages(question, [r.expanded_content for r in results])
            if body.stream:
                return _stream_response(chat_client, model, messages)
            content = chat_client.chat(messages).content
            citations = _citations(results)
    else:
        messages = [m.model_dump() for m in body.messages]  # 纯 LLM 透传
        if body.stream:
            return _stream_response(chat_client, model, messages)
        content = chat_client.chat(messages).content

    return ChatResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
        created=int(time.time()),
        model=model,
        choices=[
            ChatChoice(
                index=0,
                message=ChatChoiceMessage(
                    role="assistant", content=content, citations=citations or None
                ),
            )
        ],
        usage=ChatUsage(),
    )
