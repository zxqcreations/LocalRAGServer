"""管理端认证核心（web-admin-auth.md §1-2：Argon2id 口令 + 会话 + RBAC）。"""
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

SESSION_TTL_MINUTES = 30

_hasher = PasswordHasher()  # Argon2id 默认参数（低熵人类口令专用档，审计 M-6）


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, stored: str) -> bool:
    try:
        return _hasher.verify(stored, raw)
    except (VerifyMismatchError, Exception):  # 校验失败/哈希损坏一律拒绝
        return False


def generate_initial_password() -> str:
    """一次性初始密码（首次登录强制修改，web-admin-auth.md §1）。"""
    return secrets.token_urlsafe(12)


def hash_session(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def session_expiry(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now + timedelta(minutes=SESSION_TTL_MINUTES)
