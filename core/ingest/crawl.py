"""URL 订阅爬取（docs/design/url-crawler.md）：到期订阅重抓 + 变更重索引。

核心逻辑与 Celery 解耦（tasks.py 为薄封装），可注入 Registry 独立测试；
安全复用 UrlFetcher 全链路防护（SSRF 5 层 + 域名白名单）。
"""
import hashlib
from datetime import UTC, datetime, timedelta

from core.config import Settings, get_settings
from core.ingest.tasks import enqueue_ingest
from core.observability.logging import get_logger
from core.security.ssrf import FetchError, SsrfBlockedError, UrlFetcher
from core.storage.registry import Registry

_logger = get_logger("local_rag_server.crawl")

# 处理状态（fetch_subscription 返回值）
UNCHANGED = "unchanged"
INGESTED = "ingested"
FAILED = "failed"
SSRF_BLOCKED = "ssrf_blocked"
MISSING = "missing"


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _build_fetcher(settings: Settings) -> UrlFetcher:
    allowlist = {d.strip() for d in settings.url_allowlist.split(",") if d.strip()}
    return UrlFetcher(
        allowlist=allowlist,
        max_redirects=settings.url_fetch_max_redirects,
        max_bytes=settings.url_fetch_max_bytes,
        timeout=settings.url_fetch_timeout,
        allow_loopback=settings.url_fetch_allow_loopback,
    )


def fetch_subscription(
    registry: Registry, subscription_id: str, settings: Settings | None = None
) -> str:
    """抓取单个订阅，返回处理状态串。

    未变化：仅推进调度游标（零摄取成本）；
    变化：复用 URL 摄取链路（幂等键 kb_id+content_hash，旧版保留）；
    失败：记录错误并退避（next_fetch_at = now + interval_hours）。
    """
    settings = settings or get_settings()
    sub = registry.get_subscription(subscription_id)
    if sub is None:
        return MISSING
    now = _utcnow_naive()
    next_at = now + timedelta(hours=sub.interval_hours)

    try:
        result = _build_fetcher(settings).fetch(sub.url)
    except SsrfBlockedError as exc:
        registry.mark_subscription_error(sub.id, str(exc), now, next_at)
        _logger.warning("crawl_ssrf_blocked", kb_id=sub.kb_id, detail=str(exc))
        return SSRF_BLOCKED
    except FetchError as exc:
        registry.mark_subscription_error(sub.id, str(exc), now, next_at)
        _logger.warning("crawl_fetch_failed", kb_id=sub.kb_id, detail=str(exc))
        return FAILED

    content_hash = hashlib.sha256(result.content.encode("utf-8")).hexdigest()
    if content_hash == sub.last_content_hash:
        registry.mark_subscription_fetched(sub.id, content_hash, now, next_at)
        return UNCHANGED

    # 幂等查重：同内容已入库则只推进游标（防重复摄取）
    existing = registry.find_document_by_hash(sub.kb_id, content_hash)
    if existing is None:
        doc = registry.create_document(sub.kb_id, result.title, result.final_url, content_hash)
        job = registry.create_job(doc.id, sub.kb_id)
        work = settings.data_dir / "ingest_work" / job.id
        work.mkdir(parents=True, exist_ok=True)
        (work / "source.html").write_text(result.content, encoding="utf-8")
        enqueue_ingest(job.id)
    registry.mark_subscription_fetched(sub.id, content_hash, now, next_at)
    _logger.info("crawl_ingested", kb_id=sub.kb_id, doc_id=existing.id if existing else "")
    return INGESTED


def crawl_due(
    registry: Registry | None = None, settings: Settings | None = None
) -> list[str]:
    """扫描到期订阅并逐个抓取，返回「subscription_id:status」列表（串行，按 URL 稳定序）。"""
    settings = settings or get_settings()
    if settings.database_url is None:  # validator 已派生，fail-fast 兜底
        raise RuntimeError("database_url 未配置")
    registry = registry or Registry(settings.database_url)
    statuses: list[str] = []
    for sub in registry.list_due_subscriptions(_utcnow_naive()):
        statuses.append(f"{sub.id}:{fetch_subscription(registry, sub.id, settings)}")
    return statuses