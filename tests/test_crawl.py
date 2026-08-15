"""URL 订阅爬取测试（docs/design/url-crawler.md：变更检测/游标/失败退避契约）。"""
from datetime import timedelta

from core.config import Settings
from core.ingest.crawl import (
    FAILED,
    INGESTED,
    SSRF_BLOCKED,
    UNCHANGED,
    crawl_due,
    fetch_subscription,
)
from core.security.ssrf import html_to_text
from core.storage.registry import Registry


def _settings(tmp_path, allow_loopback=True) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "qdrant",
        database_url=f"sqlite:///{tmp_path / 'r.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        api_key="crawl-test-key",
        url_fetch_allow_loopback=allow_loopback,
    )


def _body_hash(body: str) -> str:
    import hashlib

    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _site_text_hash(fake_site) -> str:
    """与 fetch 的 content 口径一致：html_to_text 提取后的纯文本哈希。

    必须经 fake_site.RequestHandlerClass 访问（服务器实际使用的类）：
    `from tests.conftest import X` 是 pytest 双重加载的另一个模块实例，
    直接改类属性不会作用于服务中的 handler。
    """
    return _body_hash(html_to_text(fake_site.RequestHandlerClass.body.decode()))


def _set_site_body(fake_site, body: str) -> None:
    handler = fake_site.RequestHandlerClass
    handler.body = f"<html><head><title>测试</title></head><body>{body}</body></html>".encode()


def test_unchanged_content_only_advances_cursor(tmp_path, fake_site, monkeypatch):
    settings = _settings(tmp_path)
    reg = Registry(settings.database_url)
    kb = reg.create_kb("爬虫库")
    _set_site_body(fake_site, "正文内容")
    sub = reg.create_subscription(kb.id, fake_site.url, interval_hours=24)
    reg.mark_subscription_fetched(sub.id, _site_text_hash(fake_site), None, None)  # type: ignore[arg-type]
    monkeypatch.setattr("core.ingest.crawl.enqueue_ingest", lambda job_id: None)

    assert fetch_subscription(reg, sub.id, settings) == UNCHANGED
    fresh = reg.get_subscription(sub.id)
    assert fresh.last_fetched_at is not None
    assert fresh.next_fetch_at is not None
    assert reg.list_documents(kb.id) == []  # 零摄取


def test_changed_content_ingests_new_document(tmp_path, fake_site, monkeypatch):
    settings = _settings(tmp_path)
    reg = Registry(settings.database_url)
    kb = reg.create_kb("爬虫库")
    _set_site_body(fake_site, "新内容")
    sub = reg.create_subscription(kb.id, fake_site.url)
    enqueued = []
    monkeypatch.setattr("core.ingest.crawl.enqueue_ingest", enqueued.append)

    assert fetch_subscription(reg, sub.id, settings) == INGESTED
    docs = reg.list_documents(kb.id)
    assert len(docs) == 1 and docs[0].source == fake_site.url
    fresh = reg.get_subscription(sub.id)
    assert fresh.last_content_hash == _site_text_hash(fake_site)
    assert enqueued and enqueued[0]  # 任务链已入队

    # 再次抓取：内容未变 → unchanged（幂等，不重复摄取）
    assert fetch_subscription(reg, sub.id, settings) == UNCHANGED
    assert len(reg.list_documents(kb.id)) == 1


def test_fetch_error_records_and_backs_off(tmp_path, monkeypatch):
    # 注入抛 FetchError 的假 fetcher：单元隔离，不碰外联护栏
    from core.security.ssrf import FetchError

    settings = _settings(tmp_path)
    reg = Registry(settings.database_url)
    kb = reg.create_kb("爬虫库")
    sub = reg.create_subscription(kb.id, "https://unreachable.example.com/", interval_hours=6)
    monkeypatch.setattr("core.ingest.crawl.enqueue_ingest", lambda job_id: None)

    class BoomFetcher:
        def fetch(self, url):
            raise FetchError("连接超时")

    monkeypatch.setattr("core.ingest.crawl._build_fetcher", lambda s: BoomFetcher())

    assert fetch_subscription(reg, sub.id, settings) == FAILED
    fresh = reg.get_subscription(sub.id)
    assert fresh.last_error == "连接超时"  # 错误已记录
    assert fresh.next_fetch_at is not None  # 游标已推进（退避）
    assert fresh.next_fetch_at - fresh.last_fetched_at >= timedelta(hours=5)


def test_ssrf_blocked_marks_subscription(tmp_path, fake_site, monkeypatch):
    # 循环地址 + allow_loopback=False → SSRF 拦截（订阅路径同样受防护）
    settings = _settings(tmp_path, allow_loopback=False)
    reg = Registry(settings.database_url)
    kb = reg.create_kb("爬虫库")
    sub = reg.create_subscription(kb.id, fake_site.url)
    monkeypatch.setattr("core.ingest.crawl.enqueue_ingest", lambda job_id: None)

    assert fetch_subscription(reg, sub.id, settings) == SSRF_BLOCKED
    fresh = reg.get_subscription(sub.id)
    assert fresh.last_error


def test_crawl_due_scans_due_subscriptions_serially(tmp_path, fake_site, monkeypatch):
    settings = _settings(tmp_path)
    reg = Registry(settings.database_url)
    kb = reg.create_kb("爬虫库")
    _set_site_body(fake_site, "批量内容")
    sub1 = reg.create_subscription(kb.id, fake_site.url)  # 到期（无游标）
    reg.create_subscription(
        kb.id, "https://not-due.example.com/",
        next_fetch_at=__import__("datetime").datetime(2099, 1, 1),
    )
    monkeypatch.setattr("core.ingest.crawl.enqueue_ingest", lambda job_id: None)

    statuses = crawl_due(reg, settings)
    assert statuses == [f"{sub1.id}:{INGESTED}"]
    assert len(reg.list_documents(kb.id)) == 1