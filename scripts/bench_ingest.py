"""摄取吞吐基准（对照架构 §8.4；报告写入 docs/perf/ingest-bench-<date>.md）。

嵌入后端由 RAG_EMBEDDING_BACKEND 决定：stub 测管线开销；local（GPU）测真实吞吐
（Spike 已实测 bge-m3 141 条/s，本脚本给出端到端 文档/小时 口径）。

用法：uv run python scripts/bench_ingest.py --dir <目录> --limit 50
"""
import argparse
import sys
import time
from pathlib import Path

from core.config import Settings
from core.ingest.importer import _content_hash
from core.ingest.parsers import SUPPORTED_SUFFIXES
from core.ingest.pipeline import IngestPipeline
from core.retrieval.embeddings import build_embedder
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import InMemoryVectorStore

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "perf"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    settings = Settings()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_db = Path("bench_ingest.db")
    registry = Registry(f"sqlite:///{tmp_db}")
    # 基准用内存向量库：度量管线吞吐（解析/分块/嵌入），不度量磁盘 I/O
    search_service = SearchService(
        store=InMemoryVectorStore(), registry=registry, embedder=build_embedder(settings)
    )
    search_service.ensure_ready()
    pipeline = IngestPipeline(registry, search_service, settings.data_dir / "ingest_work")

    kb = registry.create_kb("bench")
    files = sorted(
        p
        for p in Path(args.dir).rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )[: args.limit]
    if not files:
        print("目录中没有可导入的文件")
        return 1

    total_chunks = 0
    start = time.perf_counter()
    for path in files:
        content_hash = _content_hash(path)
        if registry.find_document_by_hash(kb.id, content_hash) is not None:
            continue
        doc = registry.create_document(kb.id, path.name, str(path), content_hash)
        job = registry.create_job(doc.id, kb.id)
        job_dir = pipeline._job_dir(job.id)
        job_dir.joinpath(f"source{path.suffix.lower()}").write_bytes(path.read_bytes())
        pipeline.run(job.id)
        fresh = registry.get_document(kb.id, doc.id)
        if fresh is not None:
            total_chunks += fresh.chunk_count
    elapsed = time.perf_counter() - start

    docs = len(files)
    report = OUT_DIR / f"ingest-bench-{time.strftime('%Y%m%d')}.md"
    lines = [
        "# 摄取吞吐基准",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M')}",
        f"- 嵌入后端：{settings.embedding_backend}",
        f"- 文档数：{docs} · 总 chunk：{total_chunks}",
        f"- 总耗时：{elapsed:.1f}s",
        f"- **吞吐：{docs / elapsed * 3600:.0f} 文档/小时 · {total_chunks / elapsed:.1f} chunk/s**",
        "",
        "对照架构 §8.4：解析是瓶颈须多 Worker；嵌入侧 bge-m3 GPU 实测 141 条/s（sm75-matrix.md）。",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    registry.close()  # 释放 SQLite 句柄后再清理临时库
    tmp_db.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
