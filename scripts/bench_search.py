"""检索延迟压测（对照架构 §8.4：混合检索 + 重排端到端 P95 < 500ms 目标）。

用法：
  uv run python scripts/bench_search.py --docs 200 --queries 100
  （嵌入后端由 RAG_EMBEDDING_BACKEND 决定：stub 测管线开销 / GPU 测真实延迟）
"""
import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

from core.config import get_settings
from core.ingest.pipeline import IngestPipeline
from core.retrieval.embeddings import build_embedder
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import QdrantVectorStore
from scripts.bench_ingest import synth_doc

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "perf"


def _percentile(values: list[float], p: float) -> float:
    """最近秩法分位数（无需 numpy，测试友好）。"""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, int(round(p * len(ordered))))
    return ordered[min(len(ordered), rank) - 1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=200, help="预热文档数（合成 MD）")
    parser.add_argument("--queries", type=int, default=100, help="查询次数")
    args = parser.parse_args()

    settings = get_settings()
    if settings.database_url is None:
        print("database_url 未配置（应由 data_dir 派生）")
        return 1
    tmp_db = Path("bench_search.db")
    tmp_qdrant = Path(tempfile.mkdtemp(prefix="rag-search-qdrant-"))
    registry = Registry(f"sqlite:///{tmp_db}")
    embedder = build_embedder(settings)
    store = QdrantVectorStore(path=tmp_qdrant)  # 临时向量库：不污染生产数据目录
    search_service = SearchService(store, registry, embedder)
    search_service.ensure_ready()
    pipeline = IngestPipeline(registry, search_service, settings.data_dir / "ingest_work")

    # 预热：合成文档入库（内容确定性，文档 id 唯一）
    synth_root = Path(tempfile.mkdtemp(prefix="rag-search-synth-"))
    for i in range(args.docs):
        (synth_root / f"doc-{i:06d}.md").write_text(synth_doc(i), encoding="utf-8")
    kb = registry.create_kb("bench-search")
    print(f"预热 {args.docs} 篇合成文档 ...")
    for path in sorted(synth_root.glob("*.md")):
        doc = registry.create_document(kb.id, path.name, str(path), f"synth-{path.name}")
        job = registry.create_job(doc.id, kb.id)
        job_dir = pipeline._job_dir(job.id)
        job_dir.joinpath("source.md").write_bytes(path.read_bytes())
        pipeline.run(job.id)

    # 查询压测：确定性查询集轮询（涵盖词面与语义两种命中形态）
    query_pool = [
        "波导放大器的泵浦功率与净增益关系",
        "稀土离子掺杂的增益机制",
        "噪声系数与粒子数反转",
        "多模波导的模式竞争",
        "gain 函数的实现细节",
    ]
    latencies: list[float] = []
    for i in range(args.queries):
        query = query_pool[i % len(query_pool)]
        started = time.perf_counter()
        results = search_service.search(kb.id, query, top_k=5)
        elapsed = (time.perf_counter() - started) * 1000
        if not results:
            print(f"查询无结果：{query}")
            return 1
        latencies.append(elapsed)

    report = OUT_DIR / f"search-bench-{time.strftime('%Y%m%d')}.md"
    lines = [
        "# 检索延迟基准",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M')}",
        f"- 嵌入后端：{settings.embedding_backend} · 文档：{args.docs} · 查询：{args.queries}",
        f"- P50：{_percentile(latencies, 0.50):.1f}ms · "
        f"P95：**{_percentile(latencies, 0.95):.1f}ms** · "
        f"P99：{_percentile(latencies, 0.99):.1f}ms",
        f"- 均值：{statistics.mean(latencies):.1f}ms · 最大：{max(latencies):.1f}ms",
        "",
        f"对照架构 §8.4 目标：混合检索 + 重排端到端 P95 < 500ms"
        f"（当前 P95 {_percentile(latencies, 0.95):.1f}ms，"
        f"{'达标' if _percentile(latencies, 0.95) < 500 else '超限'}）。",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    registry.close()
    store.close()
    tmp_db.unlink(missing_ok=True)
    import shutil

    shutil.rmtree(synth_root, ignore_errors=True)
    shutil.rmtree(tmp_qdrant, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
