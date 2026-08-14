"""知识库路由。"""
from typing import Annotated

from fastapi import APIRouter, Depends

from apps.api.deps import get_registry
from apps.api.errors import KB_NOT_FOUND, raise_http
from apps.api.schemas import Envelope, KbCreate, KbOut, ok
from core.storage.registry import Registry

router = APIRouter()

RegistryDep = Annotated[Registry, Depends(get_registry)]


@router.post("/kb", response_model=Envelope[KbOut], status_code=201)
def create_kb(body: KbCreate, registry: RegistryDep):
    kb = registry.create_kb(body.name, body.kb_type)
    return ok(KbOut.model_validate(kb))


@router.get("/kb", response_model=Envelope[list[KbOut]])
def list_kbs(registry: RegistryDep):
    return ok([KbOut.model_validate(k) for k in registry.list_kbs()])


@router.get("/kb/{kb_id}", response_model=Envelope[KbOut])
def get_kb(kb_id: str, registry: RegistryDep):
    kb = registry.get_kb(kb_id)
    if kb is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    return ok(KbOut.model_validate(kb))
