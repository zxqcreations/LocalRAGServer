"""备份恢复演练（Phase 6 门②，docs/runbooks/backup-restore.md）。

场景 A（自动）：临时目录建库 → 摄入 → 全量备份 → 破坏 → 恢复 → 校验一致。
场景 B（检测）：恢复 SQLite 但删 Qdrant → 逐 KB 对账发现向量缺口。
报告写入 docs/perf/backup-drill-<date>.md；任一断言失败非零退出。

用法：uv run python scripts/drill_backup_restore.py
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

from core.config import Settings
from core.ingest.pipeline import IngestPipeline
from core.retrieval.embeddings import StubEmbedder
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import QdrantVectorStore

# Windows 控制台代码页（GBK/cp1252）编码不了部分字符——显式切 UTF-8（同 smoke.py）
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if _reconfigure is not None:
    _reconfigure(encoding="utf-8", errors="replace")

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "perf"


def _build(tmp: Path, seed_docs: int = 5) -> tuple[Settings, Registry, QdrantVectorStore]:
    settings = Settings(
        data_dir=tmp / "data",
        qdrant_path=tmp / "qdrant",
        database_url=f"sqlite:///{tmp / 'registry.db'}",
        embedding_backend="stub",
        embedding_dim=64,
        api_key="drill-key",
    )
    if settings.database_url is None or settings.qdrant_path is None:
        raise RuntimeError("构造参数应派生 database_url/qdrant_path（fail-fast 兜底）")
    registry = Registry(settings.database_url)
    store = QdrantVectorStore(path=settings.qdrant_path)
    service = SearchService(store, registry, StubEmbedder(dim=64))
    service.ensure_ready()
    pipeline = IngestPipeline(registry, service, settings.data_dir / "ingest_work")
    kb = registry.create_kb("演练库")
    for i in range(seed_docs):
        doc = registry.create_document(kb.id, f"doc-{i}.md", f"drill://{i}", f"hash-{i}")
        job = registry.create_job(doc.id, kb.id)
        work = settings.data_dir / "ingest_work" / job.id
        work.mkdir(parents=True, exist_ok=True)
        (work / "source.md").write_text(f"# 演练文档 {i}\n\n备份恢复演练内容。", encoding="utf-8")
        pipeline.run(job.id)
    return settings, registry, store


def _counts(registry: Registry) -> dict[str, int]:
    docs = sum(len(registry.list_documents(kb.id)) for kb in registry.list_kbs())
    chunks = 0
    for kb in registry.list_kbs():
        for d in registry.list_documents(kb.id):
            fresh = registry.get_document(kb.id, d.id)
            chunks += fresh.chunk_count if fresh is not None else 0
    return {"docs": docs, "chunks": chunks}


def _rmtree_force(path: Path) -> None:
    """强制删除：Qdrant 本地模式的 SQLite 句柄可能延迟释放（Windows 文件锁），
    gc + 短退避重试后尽力清理。"""
    import gc

    for _ in range(5):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            gc.collect()
            time.sleep(0.5)
    shutil.rmtree(path, ignore_errors=True)


def _check(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)
        print(f"  ✗ {message}")
    else:
        print(f"  ✓ {message}")


_VERIFY_CODE = """
import json, sys
from core.storage.registry import Registry
from core.storage.vector import QdrantVectorStore

def _counts(registry):
    docs = sum(len(registry.list_documents(kb.id)) for kb in registry.list_kbs())
    chunks = 0
    for kb in registry.list_kbs():
        for d in registry.list_documents(kb.id):
            fresh = registry.get_document(kb.id, d.id)
            chunks += fresh.chunk_count if fresh is not None else 0
    return {"docs": docs, "chunks": chunks}

db_url, qdrant_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
registry = Registry(db_url)
if mode == "counts":
    print(json.dumps(_counts(registry)))
else:  # mismatch：注册表 vs 向量点逐文档对账
    store = QdrantVectorStore(path=qdrant_path)
    store.ensure_collection(64)
    mismatches = []
    for kb in registry.list_kbs():
        for d in registry.list_documents(kb.id):
            fresh = registry.get_document(kb.id, d.id)
            registered = fresh.chunk_count if fresh is not None else 0
            actual = store.count(kb_id=kb.id, doc_id=d.id)
            if actual != registered:
                mismatches.append(f"{d.title}: 注册表 {registered} vs 向量 {actual}")
    print(json.dumps(mismatches))
"""


def _subprocess_verify(db_url: str, qdrant_path: str, mode: str) -> str:
    """子进程校验：Qdrant 本地模式进程内全局锁不随 close 释放（实测），
    同进程重开同一路径会撞锁——校验放子进程隔离。"""
    import subprocess  # nosec B404 -- 仅启动本解释器跑固定校验代码（Qdrant 锁隔离）

    result = subprocess.run(  # nosec B603 -- 固定 argv（本解释器 + 固定代码）
        [sys.executable, "-c", _VERIFY_CODE, db_url, qdrant_path, mode],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"子进程校验失败：{result.stderr[-500:]}")
    return result.stdout.strip()


def main() -> int:
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="rag-drill-"))
    print("=== 场景 A：全量备份 → 破坏 → 恢复 ===")
    settings, registry, store = _build(tmp)
    before = _counts(registry)
    print(f"  灾前：{before}")
    registry.close()
    store.close()
    if settings.database_url is None or settings.qdrant_path is None:
        raise RuntimeError("构造参数应派生 database_url/qdrant_path（fail-fast 兜底）")
    db_url, qdrant_path = settings.database_url, str(settings.qdrant_path)

    # 备份 data_dir（SQLite + Qdrant + ingest_work 同一次快照）
    backup = tmp / "backup"
    shutil.copytree(settings.data_dir, backup)
    _rmtree_force(settings.data_dir)  # 破坏
    _check(not (settings.data_dir).exists(), "破坏：数据目录已删除", failures)
    shutil.copytree(backup, settings.data_dir)  # 恢复
    _check((settings.data_dir).exists(), "恢复：数据目录回填", failures)

    import json as _json

    after = _json.loads(_subprocess_verify(db_url, qdrant_path, "counts"))
    _check(after == before, f"恢复后一致：{after} == {before}", failures)

    print("=== 场景 B：Qdrant 丢失检测（SQLite 完好） ===")
    _rmtree_force(settings.qdrant_path)  # 模拟向量库损坏
    mismatches = _json.loads(_subprocess_verify(db_url, qdrant_path, "mismatch"))
    _check(bool(mismatches), f"检测到向量缺口 {len(mismatches)} 处", failures)
    print("  处置路径见 docs/runbooks/backup-restore.md 场景 B（--rebuild 重索引）")

    report = OUT_DIR / f"backup-drill-{time.strftime('%Y%m%d')}.md"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "\n".join(
            [
                "# 备份恢复演练报告",
                "",
                f"- 时间：{time.strftime('%Y-%m-%d %H:%M')}",
                f"- 场景 A（全量恢复）：{'通过' if not failures else '失败'}",
                f"- 场景 B（向量缺口检测）：检测到 {len(mismatches)} 处文档不一致",
                f"- 灾前数据：{before} · 恢复后：{after}",
                "",
                "runbook：docs/runbooks/backup-restore.md",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"报告：{report}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
