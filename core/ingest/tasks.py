"""Celery 摄取任务链（契约 §4）：对 IngestPipeline 的薄封装。

本机开发用 filesystem 代理（ADR-002）；生产切 Redis 只改 broker 配置。
"""
import structlog
from celery import Celery, chain

from core.config import get_settings
from core.ingest.pipeline import MAX_ATTEMPTS, IngestPipeline
from core.observability.logging import emit_alert
from core.retrieval.embeddings import build_embedder
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import QdrantVectorStore


def _broker_transport_options() -> dict:
    """filesystem 代理目录：绝对路径 + 启动创建。

    kombu 语义：生产者写 data_folder_out，消费者读 data_folder_in——
    两侧必须指向同一目录才能配对（消费者侧默认配置还会交换 in/out，
    显式同目录配置是最稳妥形态）。
    """
    base = get_settings().data_dir / "celery"
    queue_dir = base / "queue"
    processed = base / "processed"
    for d in (queue_dir, processed):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "data_folder_in": str(queue_dir),
        "data_folder_out": str(queue_dir),
        "processed_folder": str(processed),
        "store_processed": False,
    }


# broker 来自 Settings（ADR-002：本机 filesystem，生产 Redis）；
# backend=None：任务状态持久化在 ingest_jobs 表（状态机契约 §3），无需 celery 结果后端
app = Celery(
    "local_rag_server",
    broker=get_settings().celery_broker_url,
    backend=None,
)
app.conf.broker_transport_options = _broker_transport_options()


def _pipeline() -> IngestPipeline:
    settings = get_settings()
    if settings.database_url is None:  # validator 已派生，fail-fast 兜底
        raise RuntimeError("database_url 未配置")
    registry = Registry(settings.database_url)
    embedder = build_embedder(settings)
    store = QdrantVectorStore(
        url=settings.qdrant_url,
        path=settings.qdrant_path,
        hnsw_m=settings.hnsw_m,
        hnsw_ef_construct=settings.hnsw_ef_construct,
        hnsw_ef=settings.hnsw_ef,
    )
    search_service = SearchService(
        store,
        registry,
        embedder,
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
        max_pdf_pages=settings.max_pdf_pages,
    )
    search_service.ensure_ready()
    return IngestPipeline(
        registry, search_service, settings.data_dir / "ingest_work",
        max_pdf_pages=settings.max_pdf_pages,
    )


def _handle_failure(self, job_id: str, pipeline: IngestPipeline, exc: Exception):
    """失败处理（契约 §3）：记录 failed + attempt 递增；超上限任务链终态终止（DLQ）。"""
    pipeline.mark_failed(job_id, str(exc))
    job = pipeline.registry.get_job(job_id)
    if job is not None and job.attempt >= MAX_ATTEMPTS:
        emit_alert("ingest_dlq_exhausted", job.id, level="error")
        raise  # 超过重试上限（attempt 字段承载 DLQ 标记，管理端人工处置）
    raise self.retry(exc=exc) from exc


def _bind_job_trace(job_id: str) -> None:
    """structlog-integration.md D1：worker 无 HTTP 层，任务入口绑定 trace_id=job_id
    （四阶段事件同一条 trace）；先清空防上一任务残留串扰。"""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(trace_id=job_id)


def _clear_job_trace() -> None:
    """任务结束清理（审查 L1）：防 worker 线程残留过期 trace 串扰后续任务/日志。"""
    structlog.contextvars.clear_contextvars()


@app.task(bind=True, name="ingest.parse", max_retries=MAX_ATTEMPTS)
def parse_task(self, job_id: str):
    _bind_job_trace(job_id)
    try:
        pipeline = _pipeline()
        try:
            pipeline.parse_stage(job_id)
        except Exception as exc:
            _handle_failure(self, job_id, pipeline, exc)
    finally:
        _clear_job_trace()


@app.task(bind=True, name="ingest.chunk", max_retries=MAX_ATTEMPTS)
def chunk_task(self, job_id: str):
    _bind_job_trace(job_id)
    try:
        pipeline = _pipeline()
        try:
            pipeline.chunk_stage(job_id)
        except Exception as exc:
            _handle_failure(self, job_id, pipeline, exc)
    finally:
        _clear_job_trace()


@app.task(bind=True, name="ingest.embed", max_retries=MAX_ATTEMPTS)
def embed_task(self, job_id: str):
    _bind_job_trace(job_id)
    try:
        pipeline = _pipeline()
        try:
            pipeline.embed_stage(job_id)
        except Exception as exc:
            _handle_failure(self, job_id, pipeline, exc)
    finally:
        _clear_job_trace()


@app.task(bind=True, name="ingest.index", max_retries=MAX_ATTEMPTS)
def index_task(self, job_id: str):
    _bind_job_trace(job_id)
    try:
        pipeline = _pipeline()
        try:
            pipeline.index_stage(job_id)
        except Exception as exc:
            _handle_failure(self, job_id, pipeline, exc)
    finally:
        _clear_job_trace()


def enqueue_ingest(job_id: str):
    """契约 §4：链式编排 parse → chunk → embed → index。"""
    # 按当前 Settings 刷新代理目录（模块导入时只初始化了当时的 data_dir；
    # 测试/多进程环境各自 data_dir 不同——此处幂等重建，确保队列目录存在）
    app.conf.broker_transport_options = _broker_transport_options()
    # celery 装饰后任务对象才有 .si（pyright 无法识别装饰器变换）；
    # 不可变签名：前序任务返回值不串入后续任务参数
    return chain(
        parse_task.si(job_id),  # type: ignore[attr-defined]
        chunk_task.si(job_id),  # type: ignore[attr-defined]
        embed_task.si(job_id),  # type: ignore[attr-defined]
        index_task.si(job_id),  # type: ignore[attr-defined]
    ).apply_async()


# ---------- URL 订阅爬取（docs/design/url-crawler.md；worker 加 -B 启动 beat） ----------


@app.task(name="crawl.due")
def crawl_due_task() -> list[str]:
    """beat 周期任务：扫描到期订阅并逐个抓取（串行，按 URL 稳定序）。"""
    from core.config import get_settings
    from core.ingest.crawl import crawl_due
    from core.storage.registry import Registry

    settings = get_settings()
    if settings.database_url is None:  # validator 已派生，fail-fast 兜底
        raise RuntimeError("database_url 未配置")
    registry = Registry(settings.database_url)
    try:
        return crawl_due(registry, settings)
    finally:
        registry.close()


app.conf.beat_schedule = {
    "crawl-due-every-10m": {
        "task": "crawl.due",
        "schedule": 600.0,  # 每 10 分钟扫描一次（设计：url-crawler.md）
    },
}
