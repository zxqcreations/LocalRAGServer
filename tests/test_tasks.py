"""Celery 任务链冒烟（eager 模式）。

要点：eager 模式的 retry 会内联重跑任务，若每次重建 Qdrant 客户端会触发
本地模式目录锁冲突——测试注入共享管线（monkeypatch tasks._pipeline）。
"""
import json

from celery import chain

from core.config import get_settings
from core.ingest.state import Stage


def _stage_events(capsys) -> list[dict]:
    """从捕获输出中解析 ingest_stage_done 结构化事件（structlog JSON 行）。"""
    events = []
    for line in capsys.readouterr().out.splitlines():
        try:
            payload = json.loads(line)
        except (ValueError, TypeError):
            continue
        if payload.get("event") == "ingest_stage_done":
            events.append(payload)
    return events


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("RAG_EMBEDDING_DIM", "64")
    get_settings.cache_clear()
    settings = get_settings()
    return settings


def test_ingest_chain_eager(tmp_path, monkeypatch, capsys):
    settings = _setup_env(tmp_path, monkeypatch)

    from core.ingest import tasks as tasks_mod
    from core.ingest.pipeline import IngestPipeline
    from core.retrieval.embeddings import StubEmbedder
    from core.retrieval.search import SearchService
    from core.storage.registry import Registry
    from core.storage.vector import QdrantVectorStore

    registry = Registry(settings.database_url)
    store = QdrantVectorStore(path=settings.qdrant_path)
    search_service = SearchService(store, registry, StubEmbedder(dim=64))
    search_service.ensure_ready()
    pipeline = IngestPipeline(registry, search_service, settings.data_dir / "ingest_work")
    monkeypatch.setattr(tasks_mod, "_pipeline", lambda: pipeline)

    # monkeypatch 自动还原 eager 配置（防止泄漏到后续测试）
    monkeypatch.setattr(tasks_mod.app.conf, "task_always_eager", True)
    monkeypatch.setattr(tasks_mod.app.conf, "task_eager_propagates", True)

    kb = registry.create_kb("库")
    doc = registry.create_document(kb.id, "d.md", "upload://d.md", "hash-1")
    job = registry.create_job(doc.id, kb.id)
    src = settings.data_dir / "ingest_work" / job.id / "source.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("# 标题\n\n量子计算使用量子比特与叠加态。", encoding="utf-8")

    # 链式链接用 .si()（不可变签名）：前序任务的返回值不串入后续任务参数
    result = chain(
        tasks_mod.parse_task.si(job.id),
        tasks_mod.chunk_task.si(job.id),
        tasks_mod.embed_task.si(job.id),
        tasks_mod.index_task.si(job.id),
    ).apply()
    assert result.successful(), result.result
    assert registry.get_job(job.id).stage == Stage.READY.value
    assert registry.get_document(kb.id, doc.id).chunk_count >= 1
    # structlog-integration.md D1/D2：四阶段各发一次完成事件，
    # trace_id 由任务入口绑定（= job_id），全程同一条 trace
    events = _stage_events(capsys)
    assert [e["stage"] for e in events] == ["parsed", "chunked", "embedded", "indexed"]
    assert all(e["trace_id"] == job.id for e in events)
    assert all(e["kb_id"] == kb.id and e["doc_id"] == doc.id for e in events)
    assert all(isinstance(e["duration_ms"], (int, float)) for e in events)
    registry.close()
    store.close()
    get_settings.cache_clear()


def test_ingest_chain_failure_sets_failed(tmp_path, monkeypatch):
    settings = _setup_env(tmp_path, monkeypatch)

    from core.ingest import tasks as tasks_mod
    from core.ingest.pipeline import IngestPipeline
    from core.retrieval.embeddings import StubEmbedder
    from core.retrieval.search import SearchService
    from core.storage.registry import Registry
    from core.storage.vector import QdrantVectorStore

    registry = Registry(settings.database_url)
    store = QdrantVectorStore(path=settings.qdrant_path)
    search_service = SearchService(store, registry, StubEmbedder(dim=64))
    search_service.ensure_ready()
    pipeline = IngestPipeline(registry, search_service, settings.data_dir / "ingest_work")
    monkeypatch.setattr(tasks_mod, "_pipeline", lambda: pipeline)

    monkeypatch.setattr(tasks_mod.app.conf, "task_always_eager", True)
    monkeypatch.setattr(tasks_mod.app.conf, "task_eager_propagates", False)

    kb = registry.create_kb("库")
    doc = registry.create_document(kb.id, "d.md", "upload://d.md", "hash-1")
    job = registry.create_job(doc.id, kb.id)
    # 不写 source → parse 阶段失败；eager 重试内联执行至 attempt 上限

    # eager+chain 下最终异常可能上抛（celery 边界行为）；关注点是任务终态而非传播路径
    try:
        chain(
            tasks_mod.parse_task.si(job.id),
            tasks_mod.chunk_task.si(job.id),
            tasks_mod.embed_task.si(job.id),
            tasks_mod.index_task.si(job.id),
        ).apply()
    except Exception:
        pass

    fresh = registry.get_job(job.id)
    assert fresh.stage == Stage.FAILED.value
    assert fresh.attempt >= 1
    registry.close()
    store.close()
    get_settings.cache_clear()
