"""URL 订阅注册表测试（docs/design/url-crawler.md：数据模型与调度游标契约）。"""
from datetime import UTC, datetime, timedelta

from core.storage.registry import Registry


def _now() -> datetime:
    return datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)


def _registry(tmp_path) -> Registry:
    return Registry(f"sqlite:///{tmp_path / 'r.db'}")


def test_subscription_create_and_lookup(tmp_path):
    reg = _registry(tmp_path)
    kb = reg.create_kb("订阅库")
    sub = reg.create_subscription(kb.id, "https://example.com/docs", interval_hours=6)
    assert sub.enabled is True
    assert sub.last_content_hash == ""
    assert sub.next_fetch_at is None  # 新订阅立即到期（None 视为到期）
    found = reg.get_subscription(sub.id)
    assert found is not None and found.url == "https://example.com/docs"


def test_list_subscriptions_filters_by_kb_and_enabled(tmp_path):
    reg = _registry(tmp_path)
    kb1 = reg.create_kb("库1")
    kb2 = reg.create_kb("库2")
    s1 = reg.create_subscription(kb1.id, "https://a.example.com/x")
    s2 = reg.create_subscription(kb1.id, "https://b.example.com/y")
    reg.create_subscription(kb2.id, "https://c.example.com/z")
    reg.set_subscription_enabled(s2.id, False)

    all_kb1 = reg.list_subscriptions(kb_id=kb1.id)
    assert {s.url for s in all_kb1} == {"https://a.example.com/x", "https://b.example.com/y"}
    enabled_only = reg.list_subscriptions(kb_id=kb1.id, enabled_only=True)
    assert [s.id for s in enabled_only] == [s1.id]


def test_due_subscriptions_respects_cursor_and_enabled(tmp_path):
    reg = _registry(tmp_path)
    kb = reg.create_kb("库")
    # 新订阅（无游标）立即到期
    new_sub = reg.create_subscription(kb.id, "https://new.example.com/")
    # 游标在未来 → 未到期
    future = reg.create_subscription(
        kb.id, "https://future.example.com/", next_fetch_at=_now() + timedelta(hours=1)
    )
    # 游标在过去 → 到期
    past = reg.create_subscription(
        kb.id, "https://past.example.com/", next_fetch_at=_now() - timedelta(minutes=1)
    )
    # 停用 → 永不到期
    paused = reg.create_subscription(
        kb.id, "https://paused.example.com/", next_fetch_at=_now() - timedelta(minutes=1)
    )
    reg.set_subscription_enabled(paused.id, False)

    due = reg.list_due_subscriptions(_now())
    ids = {s.id for s in due}
    assert new_sub.id in ids and past.id in ids
    assert future.id not in ids and paused.id not in ids


def test_mark_fetched_updates_cursor_and_hash(tmp_path):
    reg = _registry(tmp_path)
    kb = reg.create_kb("库")
    sub = reg.create_subscription(kb.id, "https://x.example.com/")
    next_at = _now() + timedelta(hours=24)
    reg.mark_subscription_fetched(sub.id, "hash-abc", _now(), next_at)
    fresh = reg.get_subscription(sub.id)
    assert fresh.last_content_hash == "hash-abc"
    assert fresh.last_fetched_at == _now()
    assert fresh.next_fetch_at == next_at
    assert fresh.last_error == ""


def test_mark_subscription_error_records_and_backs_off(tmp_path):
    reg = _registry(tmp_path)
    kb = reg.create_kb("库")
    sub = reg.create_subscription(kb.id, "https://x.example.com/")
    next_at = _now() + timedelta(hours=48)  # 退避
    reg.mark_subscription_error(sub.id, "连接超时", _now(), next_at)
    fresh = reg.get_subscription(sub.id)
    assert fresh.last_error == "连接超时"
    assert fresh.next_fetch_at == next_at
    assert fresh.last_content_hash == ""  # 失败不动哈希