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
from apps.api.errors import raise_http
from apps.api.schemas import Envelope, ok
from core.config import Settings
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


class KeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kb_acl: list[str] = Field(default_factory=lambda: ["*"])
    expires_in_days: int | None = None


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
    expiry = session_expiry()
    registry.create_admin_session(user.id, hash_session(session_id), expiry)
    _session_cookie(request, session_id, expiry)
    registry.record_audit(
            actor=f"admin:{user.username}",
            action="key_manage",
            kb_id="",
            ip=_client_ip(request),
        )
    return ok(
        {
            "username": user.username,
            "role": user.role,
            "must_change_password": user.must_change_password,
            "csrf_token": session_id,  # 状态变更请求以 X-CSRF-Token 头回传
        }
    )


@router.post("/change-password", response_model=Envelope[dict])
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    registry: RegistryDep,
):
    user = request.state.admin_user
    if not verify_password(body.current_password, user.password_hash):
        raise_http(401, "admin_login_failed", "当前密码错误")
    registry.set_admin_password(user.id, hash_password(body.new_password))
    registry.record_audit(
            actor=f"admin:{user.username}",
            action="key_manage",
            kb_id="",
            ip=_client_ip(request),
        )
    return ok({"changed": True})


@router.post("/logout", response_model=Envelope[dict])
def logout(request: Request, registry: RegistryDep):
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
        }
    )


@router.get("/kb", response_model=Envelope[list[dict]])
def list_kbs(registry: RegistryDep):
    return ok(
        [
            {"id": k.id, "name": k.name, "kb_type": k.kb_type}
            for k in registry.list_kbs()
        ]
    )


@router.get("/metrics", response_model=Envelope[dict])
def metrics(request: Request):
    return ok(request.app.state.metrics.snapshot())


@router.get("/audit", response_model=Envelope[list[dict]])
def list_audit(registry: RegistryDep, limit: int = 50):
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
    record, raw = registry.create_api_key(body.name, body.kb_acl, expires_at=expiry)
    registry.record_audit(
            actor=f"admin:{user.username}",
            action="key_manage",
            kb_id="",
            ip=_client_ip(request),
        )
    return ok({"id": record.id, "name": record.name, "api_key": raw})  # 明文仅此一次


@router.get("/keys", response_model=Envelope[list[dict]])
def list_keys(registry: RegistryDep):
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
    registry.record_audit(
            actor=f"admin:{user.username}",
            action="key_manage",
            kb_id="",
            ip=_client_ip(request),
        )
    return ok({"revoked": True})


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
