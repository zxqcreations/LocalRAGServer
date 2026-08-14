"""API 数据模型：统一响应信封与各路由请求/响应。"""
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

# ---------- 统一信封（用户规范：{success, data, error, meta}） ----------


class ApiError(BaseModel):
    code: str
    message: str


class Envelope(BaseModel, Generic[T]):  # noqa: UP046 — pydantic typeshed 尚未支持 PEP 695 泛型，pyright 会报 T 无意义
    success: bool
    data: T | None = None
    error: ApiError | None = None
    meta: dict | None = None


def ok(data=None, meta: dict | None = None) -> dict:
    return {"success": True, "data": data, "error": None, "meta": meta}


def err(code: str, message: str) -> dict:
    return {
        "success": False,
        "data": None,
        "error": {"code": code, "message": message},
        "meta": None,
    }


# ---------- 知识库 ----------


class KbCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kb_type: str = Field(default="document", pattern="^(document|code|web)$")


class KbOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kb_type: str
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kb_id: str
    title: str
    source: str
    status: str
    chunk_count: int
    error: str | None
    created_at: datetime


class UrlIngestRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class JobOut(BaseModel):
    id: str
    doc_id: str
    stage: str
    attempt: int


# ---------- 检索 ----------


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    kb_id: str
    top_k: int = Field(default=5, ge=1, le=50)


class SearchResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # 接受核心层 dataclass 实例

    chunk_id: str
    doc_id: str
    doc_title: str
    score: float
    dense_score: float
    content: str
    expanded_content: str


# ---------- Chat（OpenAI 兼容 + RAG 扩展字段） ----------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    rag_kb_id: str | None = None  # 扩展：指定检索知识库；为空则纯 LLM 透传
    rag_top_k: int = Field(default=5, ge=1, le=50)


class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    score: float


class ChatChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str
    citations: list[Citation] | None = None


class ChatChoice(BaseModel):
    index: int
    message: ChatChoiceMessage
    finish_reason: str = "stop"


class ChatUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoice]
    usage: ChatUsage
