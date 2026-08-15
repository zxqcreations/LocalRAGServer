"""知识库路由（列表按 ACL 过滤——KB 名称本身也是租户信息，审计 ARC-005）。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from apps.api.deps import get_allowed_kbs, get_registry
from apps.api.errors import KB_NOT_FOUND, raise_http
from apps.api.schemas import Envelope, KbCreate, KbOut, ok
from core.security.acl import AclDeniedError, AllowedKbs, require_kb_access
from core.storage.registry import Registry

router = APIRouter()

RegistryDep = Annotated[Registry, Depends(get_registry)]
AllowedKbsDep = Annotated[AllowedKbs, Depends(get_allowed_kbs)]


@router.post("/kb", response_model=Envelope[KbOut], status_code=201)
def create_kb(body: KbCreate, request: Request, registry: RegistryDep, allowed: AllowedKbsDep):
    # 管理操作：仅主 Key（allowed == "*"）授权（审计 H-1：表 Key 越权创建 → 403）
    if allowed != "*":
        raise AclDeniedError("创建知识库仅限主 Key")
    kb = registry.create_kb(body.name, body.kb_type)
    # 安全审计 M-8：KB 创建入审计
    registry.record_audit(
        actor=getattr(request.state, "actor", ""),
        action="kb_create",
        kb_id=kb.id,
        ip=request.client.host if request.client else "",
        trace_id=getattr(request.state, "trace_id", ""),
    )
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
