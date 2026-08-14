"""ACL 核心测试（审计 F-13/F-02/F-18）：哈希、校验、强制点、明文不落库。"""
import pytest

from core.security.acl import (
    AclDeniedError,
    hash_api_key,
    require_kb_access,
    resolve_allowed_kb_ids,
    verify_api_key,
)
from core.storage.registry import Registry


def test_hash_verify_roundtrip():
    stored = hash_api_key("raw-secret-key")
    assert stored != "raw-secret-key"  # 不存明文
    assert verify_api_key("raw-secret-key", stored) is True
    assert verify_api_key("wrong-key", stored) is False


def test_hash_uses_unique_salt():
    # 同一明文两次哈希结果不同（独立盐，防彩虹表）
    assert hash_api_key("same") != hash_api_key("same")


def test_resolve_acl_wildcard():
    assert resolve_allowed_kb_ids(["*"]) == "*"


def test_resolve_acl_set():
    allowed = resolve_allowed_kb_ids(["kb1", "kb2"])
    assert allowed == {"kb1", "kb2"}


def test_require_kb_access_wildcard_allows_any():
    require_kb_access("any-kb", "*")


def test_require_kb_access_granted():
    require_kb_access("kb1", {"kb1", "kb2"})


def test_require_kb_access_denied_raises_403_semantics():
    # 审计 F-13：越权必须显式拒绝（403 语义），不得静默返回空
    with pytest.raises(AclDeniedError):
        require_kb_access("kb3", {"kb1", "kb2"})


def test_require_kb_access_no_kb_deny_by_default():
    with pytest.raises(AclDeniedError):
        require_kb_access("", {"kb1"})


def test_api_key_table_no_plaintext(tmp_path):
    # 审计 F-18：DB 中无明文 Key
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    record, raw = registry.create_api_key("test-key", ["kb1"])
    assert raw  # 明文仅签发返回
    assert record.key_hash != raw
    assert raw not in record.key_hash
    verified = registry.verify_api_key(raw)
    assert verified is not None
    assert verified.kb_acl == '["kb1"]'


def test_revoked_key_fails_verification(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    record, raw = registry.create_api_key("k", ["*"])
    registry.revoke_api_key(record.id)
    assert registry.verify_api_key(raw) is None


def test_expired_key_fails_verification(tmp_path):
    import datetime

    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    record, raw = registry.create_api_key(
        "k", ["*"], expires_at=datetime.datetime.now(datetime.UTC)
    )
    assert registry.verify_api_key(raw) is None
