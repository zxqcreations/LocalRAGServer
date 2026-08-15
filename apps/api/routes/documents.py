"""文档路由：上传（同步）、URL 摄取（异步，SSRF 防护）、列表、状态查询、删除。"""
import hashlib
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile

from apps.api.deps import get_allowed_kbs, get_registry, get_search_service, get_settings
from apps.api.errors import (
    DOC_NOT_FOUND,
    EMPTY_DOCUMENT,
    FETCH_FAILED,
    INVALID_FILE_CONTENT,
    KB_NOT_FOUND,
    PAYLOAD_TOO_LARGE,
    SSRF_BLOCKED,
    TOO_MANY_PAGES,
    UNSUPPORTED_FORMAT,
    raise_http,
)
from apps.api.schemas import DocumentOut, Envelope, JobOut, UrlIngestRequest, ok
from core.config import Settings
from core.ingest.parsers import (
    SUPPORTED_SUFFIXES,
    TooManyPagesError,
    UnsupportedFormatError,
    check_signature,
)
from core.ingest.tasks import enqueue_ingest
from core.retrieval.search import EmptyDocumentError, SearchService
from core.security.acl import AllowedKbs, require_kb_access
from core.security.ssrf import FetchError, SsrfBlockedError, UrlFetcher
from core.storage.registry import Registry

router = APIRouter()

_READ_CHUNK = 1024 * 1024
_SIGNATURE_BYTES = 8

RegistryDep = Annotated[Registry, Depends(get_registry)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
AllowedKbsDep = Annotated[AllowedKbs, Depends(get_allowed_kbs)]


def _require_kb(registry: Registry, kb_id: str, allowed: AllowedKbs) -> None:
    require_kb_access(kb_id, allowed)  # ACL 强制点（摄取/删除/URL 全路径，审计 F-13）
    if registry.get_kb(kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")


@router.post("/kb/{kb_id}/documents", response_model=Envelope[DocumentOut], status_code=201)
def upload_document(
    kb_id: str,
    file: Annotated[UploadFile, File()],
    request: Request,
    registry: RegistryDep,
    search_service: SearchServiceDep,
    settings: SettingsDep,
    allowed: AllowedKbsDep,
):
    _require_kb(registry, kb_id, allowed)
    # 审计 L-3：跨平台文件名净化（反斜杠/斜杠统一替换，防 Windows 风格穿越残留）
    raw_name = file.filename or ""
    filename = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise_http(415, UNSUPPORTED_FORMAT, f"不支持的文件格式：{suffix or '(无扩展名)'}")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if file.size is not None and file.size > max_bytes:
        raise_http(413, PAYLOAD_TOO_LARGE, f"文件超过大小限制（{settings.max_upload_mb}MB）")

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    dest = Path(tmp.name)
    try:
        with tmp:
            total = 0
            while chunk := file.file.read(_READ_CHUNK):
                total += len(chunk)
                if total > max_bytes:
                    raise_http(
                        413, PAYLOAD_TOO_LARGE, f"文件超过大小限制（{settings.max_upload_mb}MB）"
                    )
                tmp.write(chunk)
        # 魔数校验：不信任客户端声明的扩展名（审计 F-07；L-2：只读头部 8 字节，不整读文件）
        with dest.open("rb") as f:
            head = f.read(_SIGNATURE_BYTES)
        if not check_signature(suffix, head):
            raise_http(415, INVALID_FILE_CONTENT, f"文件内容与扩展名 {suffix} 不符")
        doc = search_service.ingest_file(
            kb_id, dest, title=filename, source=f"upload://{filename}"
        )
        registry.record_audit(
            actor=getattr(request.state, "actor", ""),
            action="ingest",
            kb_id=kb_id,
            ip=request.client.host if request.client else "",
            trace_id=getattr(request.state, "trace_id", ""),
        )
    except UnsupportedFormatError as exc:
        raise_http(415, UNSUPPORTED_FORMAT, str(exc))
    except TooManyPagesError as exc:
        raise_http(422, TOO_MANY_PAGES, str(exc))
    except EmptyDocumentError as exc:
        raise_http(422, EMPTY_DOCUMENT, str(exc))
    finally:
        dest.unlink(missing_ok=True)
    return ok(DocumentOut.model_validate(doc))


@router.post(
    "/kb/{kb_id}/documents/url", response_model=Envelope[JobOut], status_code=202
)
def ingest_url(
    kb_id: str,
    body: UrlIngestRequest,
    request: Request,
    registry: RegistryDep,
    settings: SettingsDep,
    allowed: AllowedKbsDep,
):
    """URL 摄取（异步管线）：SSRF 防护与能力同批交付（审计 F-10）。"""
    _require_kb(registry, kb_id, allowed)
    allowlist = {d.strip() for d in settings.url_allowlist.split(",") if d.strip()}
    fetcher = UrlFetcher(
        allowlist=allowlist,
        max_redirects=settings.url_fetch_max_redirects,
        max_bytes=settings.url_fetch_max_bytes,
        timeout=settings.url_fetch_timeout,
        allow_loopback=settings.url_fetch_allow_loopback,
    )
    try:
        result = fetcher.fetch(body.url)
    except SsrfBlockedError as exc:
        raise_http(403, SSRF_BLOCKED, str(exc))
    except FetchError as exc:
        raise_http(502, FETCH_FAILED, str(exc))

    content_hash = hashlib.sha256(result.content.encode("utf-8")).hexdigest()
    doc = registry.create_document(kb_id, result.title, result.final_url, content_hash)
    job = registry.create_job(doc.id, kb_id)
    work = settings.data_dir / "ingest_work" / job.id
    work.mkdir(parents=True, exist_ok=True)
    (work / "source.html").write_text(result.content, encoding="utf-8")
    enqueue_ingest(job.id)
    registry.record_audit(
        actor=getattr(request.state, "actor", ""),
        action="ingest",
        kb_id=kb_id,
        ip=request.client.host if request.client else "",
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok(JobOut(id=job.id, doc_id=doc.id, stage=job.stage, attempt=job.attempt))


@router.get("/kb/{kb_id}/documents", response_model=Envelope[list[DocumentOut]])
def list_documents(kb_id: str, registry: RegistryDep, allowed: AllowedKbsDep):
    _require_kb(registry, kb_id, allowed)
    docs = registry.list_documents(kb_id)
    return ok([DocumentOut.model_validate(d) for d in docs], meta={"total": len(docs)})


@router.get("/kb/{kb_id}/documents/{doc_id}", response_model=Envelope[DocumentOut])
def get_document(kb_id: str, doc_id: str, registry: RegistryDep, allowed: AllowedKbsDep):
    _require_kb(registry, kb_id, allowed)
    doc = registry.get_document(kb_id, doc_id)
    if doc is None:
        raise_http(404, DOC_NOT_FOUND, "文档不存在")
    return ok(DocumentOut.model_validate(doc))


@router.delete("/kb/{kb_id}/documents/{doc_id}", response_model=Envelope[None])
def delete_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    registry: RegistryDep,
    search_service: SearchServiceDep,
    allowed: AllowedKbsDep,
):
    _require_kb(registry, kb_id, allowed)
    if registry.get_document(kb_id, doc_id) is None:
        raise_http(404, DOC_NOT_FOUND, "文档不存在")
    search_service.delete_document(kb_id, doc_id)
    registry.record_audit(
        actor=getattr(request.state, "actor", ""),
        action="delete",
        kb_id=kb_id,
        ip=request.client.host if request.client else "",
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok()
