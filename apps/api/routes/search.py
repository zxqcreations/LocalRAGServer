"""检索路由：混合检索（MVP dense-only），返回带来源的 chunk。"""
from typing import Annotated

from fastapi import APIRouter, Depends

from apps.api.deps import get_registry, get_search_service
from apps.api.errors import KB_NOT_FOUND, raise_http
from apps.api.schemas import Envelope, SearchRequest, SearchResultOut, ok
from core.retrieval.search import SearchService
from core.storage.registry import Registry

router = APIRouter()

RegistryDep = Annotated[Registry, Depends(get_registry)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]


@router.post("/search", response_model=Envelope[list[SearchResultOut]])
def search(
    body: SearchRequest,
    registry: RegistryDep,
    search_service: SearchServiceDep,
):
    if registry.get_kb(body.kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    results = search_service.search(body.kb_id, body.query, body.top_k)
    return ok(
        [SearchResultOut.model_validate(r) for r in results],
        meta={"total": len(results)},
    )
