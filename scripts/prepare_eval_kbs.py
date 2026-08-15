"""评测 KB 准备（docs/design/ragas-eval.md 前置）：按 eval-<kb_type> 命名契约
创建 KB 并同步导入 fixtures 种子语料（50 篇小文档，直接同步管线，无需 worker）。

用法：uv run python scripts/prepare_eval_kbs.py
幂等：KB 已存在则复用；文档按 content_hash 去重。
"""
import sys

from core.config import get_settings
from core.ingest.importer import import_directory
from core.ingest.pipeline import IngestPipeline
from core.retrieval.embeddings import build_embedder
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import QdrantVectorStore
from eval.dataset import FIXTURES_ROOT, KB_TYPES


def main() -> int:
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
        store, registry, embedder,
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
        max_pdf_pages=settings.max_pdf_pages,
    )
    search_service.ensure_ready()
    pipeline = IngestPipeline(
        registry, search_service, settings.data_dir / "ingest_work",
        max_pdf_pages=settings.max_pdf_pages,
    )

    total = 0
    for kb_type in KB_TYPES:
        root = FIXTURES_ROOT / kb_type
        if not root.is_dir():
            print(f"[跳过] {kb_type}：fixtures 目录不存在 {root}")
            continue
        kb = next((k for k in registry.list_kbs() if k.name == f"eval-{kb_type}"), None)
        if kb is None:
            kb = registry.create_kb(f"eval-{kb_type}", kb_type)
            print(f"[创建] {kb.name}（{kb.id}）")
        # 同步执行：fixtures 为小规模种子语料，直接管线跑完（无需 worker）
        stats = import_directory(
            registry, kb.id, str(root), settings.data_dir / "ingest_work",
            pipeline.run,
        )
        total += stats.new
        print(
            f"[导入] {kb.name}：新增 {stats.new} · "
            f"跳过(重复) {stats.skipped} · 失败 {stats.failed}"
        )
    store.close()
    registry.close()
    print(f"评测 KB 准备完成，共导入 {total} 篇")
    return 0


if __name__ == "__main__":
    sys.exit(main())
