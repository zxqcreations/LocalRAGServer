"""管理端路由（/admin/api/*，web-admin-auth.md）：会话认证，与 API Key 通道彻底隔离。

认证由 admin_middleware（main.py）完成：Cookie 会话 + CSRF token + 登录限流。
本路由内 request.state.admin_user 为已验证的管理用户。
"""
import secrets
from datetime import UTC, datetime
from datetime import timedelta as _td
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from apps.api.deps import get_registry, get_settings
from apps.api.errors import KB_NOT_FOUND, raise_http
from apps.api.schemas import Envelope, ok
from core.config import Settings
from core.security.acl import require_kb_access
from core.security.admin import (
    hash_password,
    hash_session,
    session_expiry,
    verify_password,
)
from core.storage.registry import Registry

router = APIRouter(prefix="/admin/api")

RegistryDep = Annotated[Registry, Depends(get_registry)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

COOKIE_NAME = "rag_admin_session"
CSRF_COOKIE = "rag_admin_csrf"
_ROLE_ORDER = {"readonly": 0, "admin": 1}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class AnnotationRequest(BaseModel):
    kb_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2000)
    doc_id: str = ""
    chunk_id: str = ""
    is_helpful: bool = True


class KeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kb_acl: list[str] = Field(default_factory=lambda: ["*"])
    # 安全审计 M-10：有效期上限钳制（防"百年 Key"）
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class SubscriptionCreateRequest(BaseModel):
    kb_id: str = Field(min_length=1)
    # 安全审计 M-10：netloc 拒绝 userinfo（https://user:pass@host 凭据形态不入库）
    url: str = Field(min_length=1, max_length=2048, pattern=r"^https?://[^@/]+(/.*)?$")
    interval_hours: int = Field(default=24, ge=1, le=24 * 30)


class SubscriptionToggleRequest(BaseModel):
    enabled: bool


def _require_role(request: Request, role: str) -> None:
    user = request.state.admin_user
    if user is None or _ROLE_ORDER.get(user.role, 0) < _ROLE_ORDER[role]:
        raise_http(403, "admin_forbidden", f"需要 {role} 角色")


def _session_cookie(request: Request, session_id: str, expires_at: datetime) -> None:
    # Cookie 属性契约（web-admin-auth.md §1）：
    # HttpOnly + SameSite=Lax（Secure 在 Phase 6 TLS 后启用）
    request.state.new_session_cookie = (
        COOKIE_NAME,
        session_id,
        {
            "httponly": True,
            "samesite": "lax",
            "max_age": int((expires_at - datetime.now(UTC)).total_seconds()),
            "path": "/admin",
        },
    )


@router.post("/login", response_model=Envelope[dict])
def login(
    body: LoginRequest,
    request: Request,
    registry: RegistryDep,
    settings: SettingsDep,
):
    # 登录限流在中间件完成（per-IP + per-账号）
    user = registry.get_admin_user(body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        request.state.login_failed = body.username  # 中间件记录失败尝试
        raise_http(401, "admin_login_failed", "用户名或密码错误")
    session_id = secrets.token_urlsafe(32)
    # 独立随机 CSRF token（安全审查 H-1：与 session_id 解耦，避免会话凭证孪生体落盘）
    csrf_token = secrets.token_urlsafe(32)
    expiry = session_expiry()
    registry.create_admin_session(user.id, hash_session(session_id), expiry, csrf_token)
    _session_cookie(request, session_id, expiry)
    registry.record_audit(  # 安全审计 H-3：独立动作码
        actor=f"admin:{user.username}",
        action="login",
        kb_id="",
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok(
        {
            "username": user.username,
            "role": user.role,
            "must_change_password": user.must_change_password,
            "csrf_token": csrf_token,  # 状态变更请求以 X-CSRF-Token 头回传
        }
    )


@router.post("/change-password", response_model=Envelope[dict])
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    registry: RegistryDep,
    settings: SettingsDep,
):
    user = request.state.admin_user
    if not verify_password(body.current_password, user.password_hash):
        raise_http(401, "admin_login_failed", "当前密码错误")
    # M-3：吊销其他会话，保留当前（改密请求自身会话）
    registry.set_admin_password(
        user.id,
        hash_password(body.new_password),
        exempt_session_hash=request.state.admin_session_hash,
    )
    # 安全审计 H-1：改密成功后删除初始密码明文文件（一次性凭据即用即焚）
    (settings.data_dir / "admin_initial_password").unlink(missing_ok=True)
    registry.record_audit(  # 安全审计 H-3：独立动作码
        actor=f"admin:{user.username}",
        action="password_change",
        kb_id="",
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok({"changed": True})


@router.post("/logout", response_model=Envelope[dict])
def logout(request: Request, registry: RegistryDep):
    # 安全审计 H-3：登出入审计（web-admin-auth.md §3 契约）
    registry.record_audit(
        actor=f"admin:{request.state.admin_user.username}",
        action="logout",
        kb_id="",
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    registry.revoke_admin_session(request.state.admin_session_hash)
    request.state.clear_session_cookie = True
    return ok({"logged_out": True})


@router.get("/me", response_model=Envelope[dict])
def me(request: Request):
    user = request.state.admin_user
    return ok(
        {
            "username": user.username,
            "role": user.role,
            "must_change_password": user.must_change_password,
            # 多标签页自愈：应用启动/刷新时经此取回当前会话 token（代码审查 MEDIUM-1）
            "csrf_token": getattr(request.state, "admin_csrf_token", ""),
        }
    )


@router.get("/kb", response_model=Envelope[list[dict]])
def list_kbs(registry: RegistryDep):
    """管理端 KB 列表（增强版：含文档数/碎片数）。"""
    # 延迟导入避免循环依赖
    from apps.api.routes.admin_kb import _enriched_list_compat as _elc
    return ok(_elc(registry))


@router.get("/metrics", response_model=Envelope[dict])
def metrics(request: Request):
    return ok(request.app.state.metrics.snapshot())


@router.get("/audit", response_model=Envelope[list[dict]])
def list_audit(registry: RegistryDep, limit: int = 50):
    # 安全审计 L-4：limit 上限钳制（防全量拉取打内存）
    limit = max(1, min(limit, 500))
    logs = registry.list_audit(limit=limit)
    return ok(
        [
            {
                "id": entry.id,
                "actor": entry.actor,
                "action": entry.action,
                "kb_id": entry.kb_id,
                "ip": entry.ip,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in logs
        ]
    )


@router.post("/keys", response_model=Envelope[dict], status_code=201)
def create_key(body: KeyCreateRequest, request: Request, registry: RegistryDep):
    _require_role(request, "admin")
    user = request.state.admin_user
    expiry = None
    if body.expires_in_days is not None:
        expiry = datetime.now(UTC) + _td(days=body.expires_in_days)
    # 安全审计 M-10：kb_acl 引用的 KB 必须存在（typo 不静默成空权限集）
    if body.kb_acl != ["*"]:
        for kb_id in body.kb_acl:
            if registry.get_kb(kb_id) is None:
                raise_http(422, "kb_acl_invalid", f"kb_acl 引用不存在的知识库：{kb_id}")
    record, raw = registry.create_api_key(body.name, body.kb_acl, expires_at=expiry)
    registry.record_audit(  # 安全审计 H-3：独立动作码
        actor=f"admin:{user.username}",
        action="key_create",
        kb_id="",
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok({"id": record.id, "name": record.name, "api_key": raw})  # 明文仅此一次


@router.get("/keys", response_model=Envelope[list[dict]])
def list_keys(request: Request, registry: RegistryDep):
    # 安全审计 H-4：Key 元数据（名称/ACL/使用时间）属敏感面，readonly 不可枚举
    _require_role(request, "admin")
    keys = registry.list_api_keys()
    return ok(
        [
            {
                "id": k.id,
                "name": k.name,
                "kb_acl": k.kb_acl,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            }
            for k in keys
        ]
    )


@router.delete("/keys/{key_id}", response_model=Envelope[dict])
def revoke_key(key_id: str, request: Request, registry: RegistryDep):
    _require_role(request, "admin")
    user = request.state.admin_user
    if registry.get_api_key(key_id) is None:
        raise_http(404, "key_not_found", "API Key 不存在")
    registry.revoke_api_key(key_id)
    registry.record_audit(  # 安全审计 H-3：独立动作码
        actor=f"admin:{user.username}",
        action="key_revoke",
        kb_id="",
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok({"revoked": True})


@router.post("/annotations", response_model=Envelope[dict], status_code=201)
def create_annotation(
    body: AnnotationRequest, request: Request, registry: RegistryDep
):
    # 安全审计 H-4：readonly 无任何变更操作（web-admin-auth.md §2）
    _require_role(request, "admin")
    # 审计 F17 契约：幂等覆盖（同 doc+query 重复标注覆盖而非追加）+ kb_acl 校验
    require_kb_access(body.kb_id, "*")  # 管理端全 KB 权限语义
    if registry.get_kb(body.kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    user = request.state.admin_user
    entry = registry.upsert_annotation(
        kb_id=body.kb_id,
        query=body.query,
        doc_id=body.doc_id,
        chunk_id=body.chunk_id,
        is_helpful=body.is_helpful,
        created_by=user.username,
    )
    # 安全审计 M-8：标注写入入审计（查询原文不入审计，只记动作与 KB）
    registry.record_audit(
        actor=f"admin:{user.username}",
        action="annotation_upsert",
        kb_id=body.kb_id,
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok({"id": entry.id, "kb_id": entry.kb_id, "query": entry.query})


@router.get("/annotations", response_model=Envelope[list[dict]])
def list_annotations(request: Request, registry: RegistryDep, kb_id: str, limit: int = 200):
    # 安全审计 H-4：标注含用户原始查询，readonly 不可读
    _require_role(request, "admin")
    limit = max(1, min(limit, 500))  # 安全审计 L-4：上限钳制
    if registry.get_kb(kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    return ok(
        [
            {
                "id": a.id,
                "kb_id": a.kb_id,
                "query": a.query,
                "doc_id": a.doc_id,
                "chunk_id": a.chunk_id,
                "is_helpful": a.is_helpful,
                "created_by": a.created_by,
            }
            for a in registry.list_annotations(kb_id, limit)
        ]
    )


@router.post("/search-debug", response_model=Envelope[dict])
def search_debug(body: AnnotationRequest, request: Request, registry: RegistryDep):
    # 调试台三阶段数据（审计 F17：与 search 服务同一中间结构，前端只渲染）
    if registry.get_kb(body.kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    result = request.app.state.search_service.debug_search(body.kb_id, body.query)
    return ok(result)


# ---------- URL 订阅（docs/design/url-crawler.md 实施步骤 3；仅 admin 角色） ----------


def _subscription_out(sub) -> dict:
    return {
        "id": sub.id,
        "kb_id": sub.kb_id,
        "url": sub.url,
        "interval_hours": sub.interval_hours,
        "enabled": sub.enabled,
        "last_content_hash": sub.last_content_hash,
        "last_fetched_at": sub.last_fetched_at.isoformat() if sub.last_fetched_at else None,
        "next_fetch_at": sub.next_fetch_at.isoformat() if sub.next_fetch_at else None,
        "last_error": sub.last_error,
    }


@router.post("/subscriptions", response_model=Envelope[dict], status_code=201)
def create_subscription(
    body: SubscriptionCreateRequest, request: Request, registry: RegistryDep
):
    _require_role(request, "admin")
    if registry.get_kb(body.kb_id) is None:
        raise_http(404, KB_NOT_FOUND, "知识库不存在")
    # URL 抓取的安全防护在 fetch 时执行（SSRF 5 层）；创建只做格式校验（schema pattern）
    sub = registry.create_subscription(body.kb_id, body.url, body.interval_hours)
    registry.record_audit(  # 安全审计 H-3：独立动作码
        actor=f"admin:{request.state.admin_user.username}",
        action="subscription_create",
        kb_id=body.kb_id,
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok(_subscription_out(sub))


@router.get("/subscriptions", response_model=Envelope[list[dict]])
def list_subscriptions(registry: RegistryDep, kb_id: str | None = None):
    return ok([_subscription_out(s) for s in registry.list_subscriptions(kb_id=kb_id)])


@router.post("/subscriptions/{sub_id}/toggle", response_model=Envelope[dict])
def toggle_subscription(
    sub_id: str, body: SubscriptionToggleRequest, request: Request, registry: RegistryDep
):
    _require_role(request, "admin")
    if registry.get_subscription(sub_id) is None:
        raise_http(404, "subscription_not_found", "订阅不存在")
    registry.set_subscription_enabled(sub_id, body.enabled)
    # 安全审计 M-8：订阅变更入审计
    registry.record_audit(
        actor=f"admin:{request.state.admin_user.username}",
        action="subscription_toggle",
        kb_id="",
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok(_subscription_out(registry.get_subscription(sub_id)))


@router.delete("/subscriptions/{sub_id}", response_model=Envelope[dict])
def delete_subscription(sub_id: str, request: Request, registry: RegistryDep):
    _require_role(request, "admin")
    sub = registry.get_subscription(sub_id)
    if sub is None:
        raise_http(404, "subscription_not_found", "订阅不存在")
    registry.delete_subscription(sub_id)
    # 安全审计 M-8：订阅删除入审计
    registry.record_audit(
        actor=f"admin:{request.state.admin_user.username}",
        action="subscription_delete",
        kb_id="",
        ip=_client_ip(request),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return ok({"deleted": True})


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
