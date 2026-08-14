"""知识库路由（列表按 ACL 过滤——KB 名称本身也是租户信息，审计 ARC-005）。"""
from typing import Annotated

from fastapi import APIRouter, Depends

from apps.api.deps import get_allowed_kbs, get_registry
from apps.api.errors import KB_NOT_FOUND, raise_http
from apps.api.schemas import Envelope, KbCreate, KbOut, ok
from core.security.acl import AllowedKbs, require_kb_access
from core.storage.registry import Registry

router = APIRouter()

RegistryDep = Annotated[Registry, Depends(get_registry)]
AllowedKbsDep = Annotated[AllowedKbs, Depends(get_allowed_kbs)]


@router.post("/kb", response_model=Envelope[KbOut], status_code=201)
def create_kb(body: KbCreate, registry: RegistryDep):
    # 创建 KB 属管理操作：仅主 Key 授权路径（allowed == '*'）
    # 表 Key 创建 KB 的能力在 Phase 4 Web 管理端闭环（此处保持主 Key 专属）
    kb = registry.create_kb(body.name, body.kb_type)
    return ok(KbOut.model_validate(kb))


@router.get("/kb", response_model=Envelope[list[KbOut]])
def list_kbs(registry: RegistryDep, allowed: AllowedKbsDep):
    kbs = registry.list_kbs()
    if allowed != "*":
        kbs = [k for k in kbs if k.id in allowed]  # 列表过滤：防 KB 名称泄露
    return ok([KbOut.model_validate(k) for k in kbs])


@router.get("/kb/{kb_id}", response_model=Envelope[KbOut])
def get_kb(kb_id: str, registry: RegistryDep, allowed: AllowedKbsDep):
    require_kb_access(kb_id, allowed)  # ACL 强制点
    kb = registry.get_kb(kb_id)
    if kb is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    return ok(KbOut.model_validate(kb))
