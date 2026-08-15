"""摄取吞吐基准（对照架构 §8.4；报告写入 docs/perf/ingest-bench-<date>.md）。

嵌入后端由 RAG_EMBEDDING_BACKEND 决定：stub 测管线开销；local（GPU）测真实吞吐
（Spike 已实测 bge-m3 141 条/s，本脚本给出端到端 文档/小时 口径）。

用法：
  uv run python scripts/bench_ingest.py --dir <目录> --limit 50   # 真实文件
  uv run python scripts/bench_ingest.py --synth 1000              # 合成文档（10 万级模拟）
"""
import argparse
import sys
import tempfile
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


def synth_doc(i: int) -> str:
    """确定性合成文档（模拟中英混合领域 MD：正文 + 代码段，约 500 字符）。"""
    return (
        f"# 合成文档 {i}\n\n"
        "波导放大器基于稀土离子掺杂实现信号增益，泵浦与信号共传播。"
        f"泵浦功率 {100 + i % 900} mW 时净增益约 {10 + i % 20} dB，"
        f"噪声系数受粒子数反转度 {0.5 + (i % 5) / 10:.1f} 影响。\n\n"
        "```python\n"
        f"def gain(p_pump: float) -> float:\n    return 0.1 * p_pump + {i % 7}\n"
        "```\n\n"
        "多模波导中模式竞争由增益谱与损耗谱共同决定。"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="", help="真实文件目录（与 --synth 二选一）")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--synth", type=int, default=0, help="生成 N 篇合成 MD（10 万级模拟）")
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
    synth_root: Path | None = None
    if args.synth > 0:
        synth_root = Path(tempfile.mkdtemp(prefix="rag-synth-"))
        for i in range(args.synth):
            (synth_root / f"doc-{i:06d}.md").write_text(synth_doc(i), encoding="utf-8")
        files = sorted(synth_root.glob("*.md"))
    else:
        files = sorted(
            p
            for p in Path(args.dir).rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        )[: args.limit]
    if not files:
        print("没有可导入的文件（--dir 目录为空或 --synth 为 0）")
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
    docs_per_hour = docs / elapsed * 3600 if elapsed else 0.0
    report = OUT_DIR / f"ingest-bench-{time.strftime('%Y%m%d')}.md"
    source = f"合成文档 ×{args.synth}" if synth_root else f"{Path(args.dir).name} ×{docs}"
    lines = [
        "# 摄取吞吐基准",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M')}",
        f"- 语料：{source} · 嵌入后端：{settings.embedding_backend}",
        f"- 文档数：{docs} · 总 chunk：{total_chunks}",
        f"- 总耗时：{elapsed:.1f}s",
        f"- **吞吐：{docs_per_hour:.0f} 文档/小时 · {total_chunks / elapsed:.1f} chunk/s**",
    ]
    if synth_root and args.synth < 100_000:
        # 10 万级外推（线性假设：管线开销恒定；解析侧见 §8.4 瓶颈注记）
        lines.append(
            f"- 外推：10 万文档 ≈ {100_000 / docs_per_hour:.0f} 小时"
            "（线性假设，多 Worker 线性分摊）"
        )
    lines += [
        "",
        "对照架构 §8.4：解析是瓶颈须多 Worker；嵌入侧 bge-m3 GPU 实测 141 条/s（sm75-matrix.md）。",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    registry.close()  # 释放 SQLite 句柄后再清理临时库
    tmp_db.unlink(missing_ok=True)
    if synth_root:
        import shutil

        shutil.rmtree(synth_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
