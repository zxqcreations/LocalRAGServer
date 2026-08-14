"""FastAPI 依赖：从 app.state 取服务单例与请求级 ACL。"""
from fastapi import Request

from core.config import Settings
from core.generation.llm import ChatClient
from core.retrieval.search import SearchService
from core.security.acl import AllowedKbs
from core.storage.registry import Registry


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service


def get_chat_client(request: Request) -> ChatClient:
    return request.app.state.chat_client


def get_allowed_kbs(request: Request) -> AllowedKbs:
    """请求级 ACL（由认证中间件注入；fail-closed 兜底为空集）。"""
    return getattr(request.state, "allowed_kbs", set())
