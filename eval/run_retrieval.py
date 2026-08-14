"""离线检索回归（recall@k / MRR@k，不依赖 LLM）。

审计 F9：检索侧回归是纯离线指标，Phase 2 接入 CI 作为质量门。
当前阶段仅报告结果（阈值在 Phase 2 按评测集 v1 基线确定）。

用法：uv run python -m eval.run_retrieval [--top-k 10]
"""
import argparse
import sys
import tempfile

from core.retrieval.embeddings import StubEmbedder
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import InMemoryVectorStore
from eval.dataset import FIXTURES_ROOT, KB_TYPES, load_qa, validate_dataset


def evaluate(top_k: int = 10, dim: int = 1024, chunk_size: int = 150) -> dict:
    """在种子语料上运行检索回归，返回分层与总体指标。"""
    entries = load_qa()
    errors = validate_dataset(entries)
    if errors:
        raise ValueError("评测集校验失败：\n" + "\n".join(errors))

    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    registry = Registry(f"sqlite:///{tmp_db.name}")
    service = SearchService(
        store=InMemoryVectorStore(),
        registry=registry,
        embedder=StubEmbedder(dim=dim),
        chunk_size=chunk_size,
        overlap=40,
    )
    service.ensure_ready()

    kb_ids: dict[str, str] = {}
    for kb_type in KB_TYPES:
        kb = registry.create_kb(f"eval-{kb_type}", kb_type)
        kb_ids[kb_type] = kb.id
        for path in sorted((FIXTURES_ROOT / kb_type).iterdir()):
            if path.is_file():
                service.ingest_file(kb.id, path)

    per_type: dict[str, dict] = {}
    for kb_type in KB_TYPES:
        hits = 0
        reciprocal_ranks = 0.0
        total = 0
        for e in entries:
            if e.kb_type != kb_type:
                continue
            total += 1
            results = service.search(kb_ids[kb_type], e.question, top_k)
            rank = None
            for idx, r in enumerate(results):
                if r.doc_title == e.anchor_doc and e.anchor_text in r.content:
                    rank = idx
                    break
            if rank is not None:
                hits += 1
                reciprocal_ranks += 1.0 / (rank + 1)
        per_type[kb_type] = {
            "total": total,
            f"recall@{top_k}": (hits / total) if total else 0.0,
            f"mrr@{top_k}": (reciprocal_ranks / total) if total else 0.0,
        }

    total_entries = len(entries)
    total_hits = sum(
        int(v[f"recall@{top_k}"] * v["total"]) for v in per_type.values()
    )
    total_mrr = sum(v[f"mrr@{top_k}"] * v["total"] for v in per_type.values())
    return {
        "top_k": top_k,
        "per_type": per_type,
        f"recall@{top_k}": total_hits / total_entries if total_entries else 0.0,
        f"mrr@{top_k}": total_mrr / total_entries if total_entries else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="离线检索回归（recall@k / MRR@k）")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    try:
        metrics = evaluate(top_k=args.top_k)
    except ValueError as exc:
        print(exc)
        return 1
    top_k = metrics.pop("top_k")
    for kb_type, m in metrics["per_type"].items():
        print(
            f"[{kb_type:8s}] recall@{top_k}={m[f'recall@{top_k}']:.3f} "
            f"mrr@{top_k}={m[f'mrr@{top_k}']:.3f} ({m['total']} 条)"
        )
    print(
        f"[总体     ] recall@{top_k}={metrics[f'recall@{top_k}']:.3f} "
        f"mrr@{top_k}={metrics[f'mrr@{top_k}']:.3f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
