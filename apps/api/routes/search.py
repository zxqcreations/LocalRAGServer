"""检索路由：混合检索，返回带来源的 chunk；kb_id 经 ACL 强制点（审计 F-13）。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from apps.api.deps import get_allowed_kbs, get_registry, get_search_service
from apps.api.errors import KB_NOT_FOUND, raise_http
from apps.api.schemas import Envelope, SearchRequest, SearchResultOut, ok
from core.observability.logging import get_logger
from core.retrieval.search import SearchService
from core.security.acl import AllowedKbs, require_kb_access
from core.storage.registry import Registry

router = APIRouter()
_logger = get_logger("local_rag_server.search")

RegistryDep = Annotated[Registry, Depends(get_registry)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
AllowedKbsDep = Annotated[AllowedKbs, Depends(get_allowed_kbs)]


@router.post("/search", response_model=Envelope[list[SearchResultOut]])
def search(
    body: SearchRequest,
    request: Request,
    registry: RegistryDep,
    search_service: SearchServiceDep,
    allowed: AllowedKbsDep,
):
    require_kb_access(body.kb_id, allowed)  # ACL 强制点：越权 403（不掩盖）
    if registry.get_kb(body.kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    import time as _time

    _started = _time.perf_counter()
    results = search_service.search(body.kb_id, body.query, body.top_k)
    duration_ms = (_time.perf_counter() - _started) * 1000
    request.app.state.metrics.observe("search.latency_ms", duration_ms)
    # structlog-integration.md D2：检索事件（trace_id 由中间件 contextvars 携带；
    # 查询文本不落日志——白名单机制兜底，此处也不 bind）
    _logger.info("search_ok", kb_id=body.kb_id, hits=len(results), duration_ms=duration_ms)
    # 审计埋点（F-05：检索事件；查询文本不落日志，仅记录动作与 KB）
    registry.record_audit(
        actor=getattr(request.state, "actor", ""),
        action="search",
        kb_id=body.kb_id,
        ip=request.client.host if request.client else "",
    )
    return ok(
        [SearchResultOut.model_validate(r) for r in results],
        meta={"total": len(results)},
    )
