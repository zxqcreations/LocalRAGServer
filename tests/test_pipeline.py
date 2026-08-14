"""摄取管线测试（审计 F4：状态转移/幂等/断点恢复/对账失败）。"""
import pytest

from core.ingest.pipeline import IngestPipeline
from core.ingest.state import Stage
from core.retrieval.embeddings import StubEmbedder
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import InMemoryVectorStore


@pytest.fixture
def env(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    search_service = SearchService(
        store=InMemoryVectorStore(), registry=registry, embedder=StubEmbedder(dim=64)
    )
    search_service.ensure_ready()
    pipeline = IngestPipeline(registry, search_service, tmp_path / "work")
    kb = registry.create_kb("库")
    return registry, search_service, pipeline, kb


def _new_job(env, tmp_path, content="# 标题\n\n量子计算使用量子比特与叠加态。"):
    registry, _, pipeline, kb = env
    doc = registry.create_document(kb.id, "d.md", "upload://d.md", "hash-1")
    assert doc.status == Stage.UPLOADED.value  # 异步流文档初始为 uploaded
    job = registry.create_job(doc.id, kb.id)
    src = pipeline._job_dir(job.id) / "source.md"
    src.write_text(content, encoding="utf-8")
    return job, doc


def test_full_run_reaches_ready_and_searchable(env, tmp_path):
    registry, search_service, pipeline, kb = env
    job, doc = _new_job(env, tmp_path)
    pipeline.run(job.id)
    fresh = registry.get_job(job.id)
    assert fresh.stage == Stage.READY.value
    doc_fresh = registry.get_document(kb.id, doc.id)
    assert doc_fresh.status == "ready"
    assert doc_fresh.pipeline_version is not None  # 审计 ARC-003：管线版本戳已记录
    results = search_service.search(kb.id, "量子比特", top_k=3)
    assert results and results[0].doc_id == doc.id


def test_run_is_idempotent(env, tmp_path):
    registry, _, pipeline, kb = env
    job, doc = _new_job(env, tmp_path)
    pipeline.run(job.id)
    chunk_ids = registry.get_document(kb.id, doc.id).chunk_count
    pipeline.run(job.id)  # 重跑：全部阶段幂等跳过
    fresh = registry.get_document(kb.id, doc.id)
    assert fresh.chunk_count == chunk_ids  # 无重复 chunk
    assert registry.get_job(job.id).stage == Stage.READY.value


def test_failure_resume_from_failed_stage(env, tmp_path):
    registry, _, pipeline, kb = env
    job, doc = _new_job(env, tmp_path, content="")
    # parse 阶段产物缺失文本 → chunk 阶段应失败
    with pytest.raises(ValueError):
        pipeline.run(job.id)
    # 修正：写入有效文本后，从失败阶段恢复
    src = pipeline._job_dir(job.id) / "source.md"
    src.write_text("恢复后的有效内容：量子计算使用量子比特。", encoding="utf-8")
    pipeline.run(job.id)
    assert registry.get_job(job.id).stage == Stage.READY.value
    assert registry.get_document(kb.id, doc.id).chunk_count >= 1


def test_index_stage_reconciliation_failure(env, tmp_path):
    registry, search_service, pipeline, kb = env

    class FaultyStore(InMemoryVectorStore):
        def count(self, kb_id, doc_id):
            return 0  # 模拟对账不一致

    pipeline._search = SearchService(
        store=FaultyStore(), registry=registry, embedder=StubEmbedder(dim=64)
    )
    pipeline._search.ensure_ready()
    job, _ = _new_job(env, tmp_path)
    with pytest.raises(RuntimeError, match="对账失败"):
        pipeline.run(job.id)
    fresh = registry.get_job(job.id)
    assert fresh.stage == Stage.FAILED.value
    assert fresh.attempt == 1
    assert fresh.failed_at == Stage.INDEXED.value  # 失败发生阶段被记录


def test_mark_failed_records_error(env, tmp_path):
    registry, _, pipeline, kb = env
    job, _ = _new_job(env, tmp_path)
    pipeline.mark_failed(job.id, "测试错误")
    fresh = registry.get_job(job.id)
    assert fresh.stage == Stage.FAILED.value
    assert "测试错误" in fresh.error
    assert fresh.attempt == 1
