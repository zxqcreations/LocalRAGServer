"""摄取管线（契约 docs/design/ingest-state-machine.md §4）：状态机驱动四阶段。

Celery 任务为薄封装；本类可独立测试（注入 Registry/SearchService/工作目录）。
工作目录布局（data_dir/ingest_work/{job_id}/）：
  source        上传时拷贝的原始文件
  text.txt      parse 阶段产物（解析文本）
  chunks.json   chunk 阶段产物（chunk id 列表）
  vecs.json     embed 阶段产物（chunk_id -> 向量）
"""
import hashlib
import json
import time
from pathlib import Path

from core.ingest.chunker import chunk_text
from core.ingest.parsers import parse_file
from core.ingest.state import Stage, retry_from, stage_reached
from core.observability.logging import get_logger
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import VectorPoint

_logger = get_logger("local_rag_server.ingest")

MAX_ATTEMPTS = 5  # 契约 §3：超限进 DLQ（attempt 字段承载）

# 管线版本戳（审计 ARC-003）：parser/chunker/embedder 三元组任一变更必须递增
PIPELINE_VERSION = "parsers-1.1/chunker-1.0/embedder-settings"


class IngestPipeline:
    def __init__(
        self,
        registry: Registry,
        search_service: SearchService,
        work_dir: Path,
        max_pdf_pages: int | None = None,
    ) -> None:
        self.registry = registry
        self._search = search_service
        self._work = work_dir
        self._max_pdf_pages = max_pdf_pages
        self._work.mkdir(parents=True, exist_ok=True)

    # ---------- 工具 ----------

    def _job_dir(self, job_id: str) -> Path:
        d = self._work / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _require_job(self, job_id: str):
        job = self.registry.get_job(job_id)
        if job is None:
            raise LookupError(f"任务不存在：{job_id}")
        return job

    def _resume_if_failed(self, job_id: str) -> None:
        """契约 §2.3/v1.1：FAILED 重新入队时回到失败发生阶段重跑。"""
        job = self._require_job(job_id)
        if job.stage != Stage.FAILED.value:
            return
        failed_at = Stage(job.failed_at) if job.failed_at else Stage.UPLOADED
        self.registry.transition_job(job_id, retry_from(failed_at))

    def mark_failed(self, job_id: str, error: str) -> None:
        # transition 同时记录 failed_at（失败发生阶段）与 attempt 递增（契约 §3）
        self.registry.transition_job(job_id, Stage.FAILED, error=error)

    # ---------- 四阶段（幂等：已到达阶段且产物哈希一致才跳过） ----------

    @staticmethod
    def _hash_file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _emit_stage(self, job, stage: str, started: float) -> None:
        """structlog-integration.md D2：阶段完成事件。

        仅在真实完成时发（幂等跳过不发，防重试/重启刷日志）；
        trace_id 由 worker 任务入口绑定（= job_id，四阶段同一条 trace）。
        """
        _logger.info(
            "ingest_stage_done",
            kb_id=job.kb_id,
            doc_id=job.doc_id,
            stage=stage,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def _stale(self, job_id: str, artifact: str, upstream: Path, meta: str) -> bool:
        """产物缺失或上游文件哈希与记录的元哈希不一致 => 需要重算。"""
        d = self._job_dir(job_id)
        artifact_path = d / artifact
        meta_path = d / meta
        if not artifact_path.exists() or not meta_path.exists():
            return True
        return meta_path.read_text(encoding="utf-8").strip() != self._hash_file(upstream)

    def parse_stage(self, job_id: str) -> None:
        started = time.perf_counter()
        self._resume_if_failed(job_id)
        job = self._require_job(job_id)
        # source 保留原始扩展名（解析器按后缀路由）；上传时落盘为 source.<ext>
        sources = list(self._job_dir(job_id).glob("source.*"))
        if not sources:
            raise FileNotFoundError(f"任务 {job_id} 缺少源文件（source.*）")
        if stage_reached(Stage(job.stage), Stage.PARSED) and not self._stale(
            job_id, "text.txt", sources[0], "text.txt.sha256"
        ):
            return
        parsed = parse_file(sources[0], max_pages=self._max_pdf_pages)
        d = self._job_dir(job_id)
        (d / "text.txt").write_text(parsed.text, encoding="utf-8")
        (d / "text.txt.sha256").write_text(self._hash_file(sources[0]), encoding="utf-8")
        self.registry.transition_job(job_id, Stage.PARSED)
        self._emit_stage(job, Stage.PARSED.value, started)

    def chunk_stage(self, job_id: str) -> None:
        started = time.perf_counter()
        self._resume_if_failed(job_id)
        job = self._require_job(job_id)
        d = self._job_dir(job_id)
        if stage_reached(Stage(job.stage), Stage.CHUNKED) and not self._stale(
            job_id, "chunks.json", d / "text.txt", "chunks.json.sha256"
        ):
            return
        text = (d / "text.txt").read_text(encoding="utf-8")
        chunks = chunk_text(text, self._search.chunk_size, self._search.overlap)
        if not chunks:
            raise ValueError("解析后无文本内容")
        chunk_ids = self.registry.set_chunks(job.doc_id, job.kb_id, chunks)
        # 记录管线版本戳（审计 ARC-003：策略变更后据此判定重索引）
        self.registry.set_pipeline_version(job.doc_id, PIPELINE_VERSION)
        (d / "chunks.json").write_text(json.dumps(chunk_ids, ensure_ascii=False), encoding="utf-8")
        (d / "chunks.json.sha256").write_text(self._hash_file(d / "text.txt"), encoding="utf-8")
        self.registry.transition_job(job_id, Stage.CHUNKED)
        self._emit_stage(job, Stage.CHUNKED.value, started)

    def embed_stage(self, job_id: str) -> None:
        started = time.perf_counter()
        self._resume_if_failed(job_id)
        job = self._require_job(job_id)
        d = self._job_dir(job_id)
        if stage_reached(Stage(job.stage), Stage.EMBEDDED) and not self._stale(
            job_id, "vecs.json", d / "chunks.json", "vecs.json.sha256"
        ):
            return
        chunk_ids = json.loads((d / "chunks.json").read_text(encoding="utf-8"))
        contents = self.registry.get_chunk_contents(chunk_ids)
        vectors = self._search.embedder.embed([contents[cid] for cid in chunk_ids])
        payload = {cid: vector for cid, vector in zip(chunk_ids, vectors, strict=True)}
        (d / "vecs.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (d / "vecs.json.sha256").write_text(
            self._hash_file(d / "chunks.json"), encoding="utf-8"
        )
        self.registry.transition_job(job_id, Stage.EMBEDDED)
        self._emit_stage(job, Stage.EMBEDDED.value, started)

    def index_stage(self, job_id: str) -> None:
        started = time.perf_counter()
        self._resume_if_failed(job_id)
        job = self._require_job(job_id)
        if stage_reached(Stage(job.stage), Stage.INDEXED):
            return
        d = self._job_dir(job_id)
        chunk_ids = json.loads((d / "chunks.json").read_text(encoding="utf-8"))
        vectors = json.loads((d / "vecs.json").read_text(encoding="utf-8"))
        points = [
            VectorPoint(
                id=cid,
                vector=vectors[cid],
                payload={"kb_id": job.kb_id, "doc_id": job.doc_id},
            )
            for cid in chunk_ids
        ]
        self._search.store.upsert(points)
        self.registry.transition_job(job_id, Stage.INDEXED)
        # 对账（契约 §2.5：indexed 状态写入后核对）：向量点数与注册表 chunk 数一致
        actual = self._search.store.count(kb_id=job.kb_id, doc_id=job.doc_id)
        if actual != len(chunk_ids):
            raise RuntimeError(f"对账失败：Qdrant {actual} 点 != 注册表 {len(chunk_ids)} chunk")
        self.registry.transition_job(job_id, Stage.READY)
        self._emit_stage(job, Stage.INDEXED.value, started)

    def run(self, job_id: str) -> None:
        """全链路执行（测试/冒烟用）；阶段失败即转 FAILED（与任务行为一致，契约 §2.4）。"""
        stages = (
            self.parse_stage,
            self.chunk_stage,
            self.embed_stage,
            self.index_stage,
        )
        for stage_fn in stages:
            try:
                stage_fn(job_id)
            except Exception as exc:
                self.mark_failed(job_id, str(exc))
                raise
