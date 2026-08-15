"""管理端认证核心测试（web-admin-auth.md 契约：Argon2id/会话哈希/幂等/吊销）。"""

from core.security.admin import (
    generate_initial_password,
    hash_password,
    hash_session,
    session_expiry,
    verify_password,
)
from core.storage.registry import Registry


def test_argon2id_password_roundtrip():
    stored = hash_password("human-password-123")
    assert stored != "human-password-123"
    assert stored.startswith("$argon2id")
    assert verify_password("human-password-123", stored) is True
    assert verify_password("wrong", stored) is False


def test_verify_password_survives_corrupt_hash():
    assert verify_password("x", "not-a-valid-hash") is False


def test_initial_password_is_random_and_long():
    a, b = generate_initial_password(), generate_initial_password()
    assert a != b
    assert len(a) >= 16


def test_session_hash_is_deterministic_sha256():
    assert hash_session("sid") == hash_session("sid")
    assert hash_session("sid") != "sid"  # 不存明文


def test_session_expiry_default_ttl():
    from datetime import UTC, datetime

    expiry = session_expiry(datetime.now(UTC))
    assert (expiry - datetime.now(UTC)).total_seconds() > 29 * 60


def test_admin_user_lifecycle(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    user = registry.ensure_admin_user("admin", hash_password("init-pass"))
    assert user.must_change_password is True
    # 幂等：重复创建返回既有记录
    again = registry.ensure_admin_user("admin", hash_password("other"))
    assert again.id == user.id
    # 改密：must_change_password 清零
    registry.set_admin_password(user.id, hash_password("new-pass"))
    fresh = registry.get_admin_user("admin")
    assert fresh.must_change_password is False
    assert verify_password("new-pass", fresh.password_hash) is True


def test_admin_session_lifecycle(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    user = registry.ensure_admin_user("admin", hash_password("p"))
    session_hash = hash_session("session-id-1")
    # 安全审查 H-1：独立随机 CSRF token 随会话持久化
    registry.create_admin_session(
        user.id, session_hash, session_expiry(), csrf_token="csrf-t-1"
    )
    found = registry.find_admin_session(session_hash)
    assert found is not None and found.user_id == user.id
    assert found.csrf_token == "csrf-t-1"
    registry.revoke_admin_session(session_hash)
    assert registry.find_admin_session(session_hash) is None


def test_schema_self_heal_adds_csrf_token_column(tmp_path):
    # 老库升级：adminsession 表缺 csrf_token 列时，Registry 初始化补列（幂等）
    from sqlalchemy import create_engine, inspect, text

    db = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE adminsession ("
                "id VARCHAR PRIMARY KEY, user_id VARCHAR, session_hash VARCHAR, "
                "expires_at DATETIME, created_at DATETIME)"
            )
        )
    Registry(f"sqlite:///{db}")  # 触发 create_all + 自愈
    columns = {c["name"] for c in inspect(engine).get_columns("adminsession")}
    assert "csrf_token" in columns
