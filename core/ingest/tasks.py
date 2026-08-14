"""Celery 摄取任务链（契约 §4）：对 IngestPipeline 的薄封装。

本机开发用 filesystem 代理（ADR-002）；生产切 Redis 只改 broker 配置。
"""
from celery import Celery, chain

from core.config import get_settings
from core.ingest.pipeline import MAX_ATTEMPTS, IngestPipeline
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
        raise  # 超过重试上限（attempt 字段承载 DLQ 标记，管理端人工处置）
    raise self.retry(exc=exc) from exc


@app.task(bind=True, name="ingest.parse", max_retries=MAX_ATTEMPTS)
def parse_task(self, job_id: str):
    pipeline = _pipeline()
    try:
        pipeline.parse_stage(job_id)
    except Exception as exc:
        _handle_failure(self, job_id, pipeline, exc)


@app.task(bind=True, name="ingest.chunk", max_retries=MAX_ATTEMPTS)
def chunk_task(self, job_id: str):
    pipeline = _pipeline()
    try:
        pipeline.chunk_stage(job_id)
    except Exception as exc:
        _handle_failure(self, job_id, pipeline, exc)


@app.task(bind=True, name="ingest.embed", max_retries=MAX_ATTEMPTS)
def embed_task(self, job_id: str):
    pipeline = _pipeline()
    try:
        pipeline.embed_stage(job_id)
    except Exception as exc:
        _handle_failure(self, job_id, pipeline, exc)


@app.task(bind=True, name="ingest.index", max_retries=MAX_ATTEMPTS)
def index_task(self, job_id: str):
    pipeline = _pipeline()
    try:
        pipeline.index_stage(job_id)
    except Exception as exc:
        _handle_failure(self, job_id, pipeline, exc)


def enqueue_ingest(job_id: str):
    """契约 §4：链式编排 parse → chunk → embed → index。"""
    # celery 装饰后任务对象才有 .si（pyright 无法识别装饰器变换）；
    # 不可变签名：前序任务返回值不串入后续任务参数
    return chain(
        parse_task.si(job_id),  # type: ignore[attr-defined]
        chunk_task.si(job_id),  # type: ignore[attr-defined]
        embed_task.si(job_id),  # type: ignore[attr-defined]
        index_task.si(job_id),  # type: ignore[attr-defined]
    ).apply_async()
