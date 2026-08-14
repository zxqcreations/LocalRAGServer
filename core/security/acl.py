"""ACL 核心（审计 F-13/F-02）：Key 哈希、强制点、403 语义。

设计契约：docs/design/acl-enforcement.md——kb_id 最终过滤值只由服务端推导。
"""
import hashlib
import secrets
from typing import Literal

SCRYPT_N = 2**14  # scrypt 参数（CPU/内存权衡；本地服务合理档位）
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_PREFIX_LEN = 8  # 前缀索引长度（先查前缀再验哈希，避免全表扫描）


class AclDeniedError(PermissionError):
    """越权访问（路由层映射为 403）。"""


def hash_api_key(raw: str) -> str:
    """scrypt 慢哈希 + 独立盐；存储格式 salt$hash（hex）。"""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        raw.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    return f"{salt.hex()}${digest.hex()}"


def verify_api_key(raw: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    actual = hashlib.scrypt(raw.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return secrets.compare_digest(actual, expected)


def key_prefix(raw: str) -> str:
    return raw[:KEY_PREFIX_LEN]


AllowedKbs = set[str] | Literal["*"]


def resolve_allowed_kb_ids(acl: list[str]) -> AllowedKbs:
    """解析 ACL；'*' 表示全部 KB。"""
    if "*" in acl:
        return "*"
    return set(acl)


def require_kb_access(kb_id: str, allowed: AllowedKbs) -> None:
    """强制点（单一入口）：请求 kb_id 必须落在 ACL 内；未指定 kb_id 拒绝（deny by default）。"""
    if not kb_id:
        raise AclDeniedError("未指定知识库（deny by default）")
    if allowed == "*":
        return
    if kb_id not in allowed:
        raise AclDeniedError(f"无权限访问知识库：{kb_id}")
