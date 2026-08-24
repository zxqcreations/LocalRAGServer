"""知识库管理端路由（/admin/api/*，session 认证）。

与公开 REST API 通道完全隔离——不经过 Bearer token 中间件。
写操作需 admin 角色，读操作任何已登录用户可用。
"""
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from pydantic import BaseModel, Field

from apps.api.deps import get_registry, get_search_service, get_settings
from apps.api.errors import KB_NOT_FOUND, raise_http
from apps.api.schemas import DocumentOut, Envelope, KbOut, KbUpdate, ok
from apps.api.routes.admin import _require_role
from core.config import Settings
from core.ingest.parsers import SUPPORTED_SUFFIXES, check_signature
from core.retrieval.search import SearchService
from core.storage.registry import Registry

RegistryDep = Annotated[Registry, Depends(get_registry)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]

router = APIRouter(prefix="/admin/api")


# ---------- 兼容 admin.py 的延迟调用 ----------


def _enriched_list_compat(registry):
    """供 admin.py 通过延迟导入调用的增强 KB 列表（避免循环依赖）。"""
    return registry.list_kbs_enriched()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


# ---------- KB CRUD ----------


@router.post("/kb", response_model=Envelope[KbOut], status_code=201)
def create_kb(body: KbUpdate, request: Request, registry: RegistryDep):
    """创建知识库（仅 admin 角色）。"""
    _require_role(request, "admin")
    name = body.name.strip() if body.name else ""
    kb_type = body.kb_type or "document"
    description = body.description or ""
    if not name or len(name) < 1 or len(name) > 100:
        raise_http(422, "invalid_name", "知识库名称必须为 1-100 个字符")
    if kb_type not in ("document", "code", "web"):
        raise_http(422, "invalid_kb_type", "kb_type 必须是 document / code / web")
    kb = registry.create_kb(name=name, kb_type=kb_type)
    # 首次写入描述（create_kb 无 description 参数）
    if description:
        registry.update_kb(kb.id, description=description)
    registry.record_audit(
        actor=f"admin:{request.state.admin_user.username}",
        action="kb_create",
        kb_id=kb.id,
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    out = KbOut.model_validate(kb)
    return ok(out)


@router.get("/kb/stats", response_model=Envelope[list[dict]])
def list_kb_stats(registry: RegistryDep):
    """列出所有 KB 及其统计信息（any 角色可用）。"""
    return ok(registry.list_kbs_enriched())


@router.get("/kb/{kb_id}", response_model=Envelope[KbOut])
def get_kb_detail(kb_id: str, request: Request, registry: RegistryDep):
    """获取单 KB 详情含元数据+统计（any 角色可用）。"""
    from core.security.acl import require_kb_access

    try:
        require_kb_access(kb_id, "*")  # 管理端全 KB 权限语义
    except Exception:
        pass  # 管理端 bypass ACL check for reads
    kb = registry.get_kb(kb_id)
    if kb is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    stats = registry.get_kb_stats(kb_id)
    data = KbOut.model_validate(kb)
    data.doc_count = stats["doc_count"]
    data.chunk_count = stats["chunk_count"]
    data.failed_count = stats["failed_count"]
    return ok(data)


@router.put("/kb/{kb_id}", response_model=Envelope[KbOut])
def update_kb(kb_id: str, body: KbUpdate, request: Request, registry: RegistryDep):
    """更新知识库（仅 admin 角色，部分更新）。"""
    _require_role(request, "admin")
    user = request.state.admin_user
    name = body.name if body.name.strip() != "" else None
    kb_type_val = body.kb_type
    kb_type = None if kb_type_val == "document" else kb_type_val
    description = body.description
    if name is not None and (len(name) < 1 or len(name) > 100):
        raise_http(422, "invalid_name", "知识库名称必须为 1-100 个字符")
    if len(description) > 500:
        raise_http(422, "description_too_long", "简介不能超过 500 字符")
    updated = registry.update_kb(kb_id, name=name, kb_type=kb_type, description=description)
    registry.record_audit(
        actor=f"admin:{user.username}",
        action="kb_update",
        kb_id=kb_id,
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok(KbOut.model_validate(updated))


@router.delete("/kb/{kb_id}", response_model=Envelope[dict])
def delete_kb_route(kb_id: str, request: Request, registry: RegistryDep):
    """级联删除 KB（仅 admin 角色）。"""
    _require_role(request, "admin")
    user = request.state.admin_user
    kb = registry.get_kb(kb_id)
    if kb is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    registry.record_audit(
        actor=f"admin:{user.username}",
        action="kb_delete",
        kb_id=kb_id,
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    registry.delete_kb(kb_id)
    return ok({"deleted": True})


# ---------- Documents (admin view) ----------


@router.get("/kb/{kb_id}/documents", response_model=Envelope[list[DocumentOut]])
def list_kb_documents(kb_id: str, registry: RegistryDep):
    """列出某 KB 下的所有文档（any 角色可用）。"""
    from core.security.acl import require_kb_access

    try:
        require_kb_access(kb_id, "*")
    except Exception:
        pass
    if registry.get_kb(kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    docs = registry.list_documents(kb_id)
    return ok([DocumentOut.model_validate(d) for d in docs])


@router.delete("/kb/{kb_id}/documents/{doc_id}", response_model=Envelope[dict])
def delete_kb_document(kb_id: str, doc_id: str, request: Request, registry: RegistryDep):
    """删除指定文档（仅 admin 角色）。"""
    _require_role(request, "admin")
    user = request.state.admin_user
    if registry.get_kb(kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    doc = registry.get_document(kb_id, doc_id)
    if doc is None:
        raise_http(404, "document_not_found", "文档不存在")
    registry.record_audit(
        actor=f"admin:{user.username}",
        action="document_delete",
        kb_id=kb_id,
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    registry.delete_document(kb_id, doc_id)
    return ok({"deleted": True})


# ---------- File Upload (admin side, session auth) ----------


import base64


class FileUploadRequest(BaseModel):
    """JSON 文件上传请求体（通过 base64 传输文件内容）。"""
    filename: str = Field(min_length=1, max_length=256)
    data: str = Field(min_length=1)  # base64 编码的文件内容


def _process_uploaded_file(
    kb_id: str,
    filename: str,
    content: bytes,
    settings: SettingsDep,
    search_service: SearchServiceDep,
    registry: RegistryDep,
    request: Request,
):
    """通用的文件处理逻辑（解析 + 分块 + 向量化）。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise_http(415, "unsupported_format", f"不支持的文件格式：{suffix or '(无扩展名)'}")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise_http(413, "payload_too_large", f"文件超过大小限制（{settings.max_upload_mb}MB）")
    # 魔数校验（只读头部 8 字节）
    if not check_signature(suffix, content[:8]):
        raise_http(415, "invalid_content", f"文件内容与扩展名 {suffix} 不符")
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    dest = Path(tmp.name)
    try:
        dest.write_bytes(content)
        doc = search_service.ingest_file(
            kb_id, dest, title=filename, source=f"upload://{filename}"
        )
        registry.record_audit(
            actor=f"admin:{request.state.admin_user.username}",
            action="document_upload",
            kb_id=kb_id,
            ip=request.client.host if request.client else "",
            trace_id=getattr(request.state, "trace_id", ""),
        )
        return ok(DocumentOut.model_validate(doc))
    except Exception as exc:
        raise_http(500, "upload_failed", f"文件处理失败：{exc}")
    finally:
        dest.unlink(missing_ok=True)


# ---------- File Upload (admin side, session auth) ----------


@router.post("/kb/{kb_id}/documents/upload-json", response_model=Envelope[DocumentOut], status_code=201)
async def upload_document_json(
    kb_id: str,
    body: FileUploadRequest,
    request: Annotated[Request, Depends()],
    registry: RegistryDep,
    settings: SettingsDep,
    search_service: SearchServiceDep,
):
    """管理端文件上传（JSON + base64，兼容代理；session 认证）。"""
    _require_role(request, "admin")
    if registry.get_kb(kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    content = base64.b64decode(body.data)
    return _process_uploaded_file(
        kb_id, body.filename, content, settings, search_service, registry, request
    )
