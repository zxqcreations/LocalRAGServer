"""检索路由：混合检索，返回带来源的 chunk；kb_id 经 ACL 强制点（审计 F-13）。"""
from typing import Annotated

from fastapi import APIRouter, Depends

from apps.api.deps import get_allowed_kbs, get_registry, get_search_service
from apps.api.errors import KB_NOT_FOUND, raise_http
from apps.api.schemas import Envelope, SearchRequest, SearchResultOut, ok
from core.retrieval.search import SearchService
from core.security.acl import AllowedKbs, require_kb_access
from core.storage.registry import Registry

router = APIRouter()

RegistryDep = Annotated[Registry, Depends(get_registry)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
AllowedKbsDep = Annotated[AllowedKbs, Depends(get_allowed_kbs)]


@router.post("/search", response_model=Envelope[list[SearchResultOut]])
def search(
    body: SearchRequest,
    registry: RegistryDep,
    search_service: SearchServiceDep,
    allowed: AllowedKbsDep,
):
    require_kb_access(body.kb_id, allowed)  # ACL 强制点：越权 403（不掩盖）
    if registry.get_kb(body.kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    results = search_service.search(body.kb_id, body.query, body.top_k)
    return ok(
        [SearchResultOut.model_validate(r) for r in results],
        meta={"total": len(results)},
    )
