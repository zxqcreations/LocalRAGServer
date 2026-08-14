"""批量导入（Phase 1 质量门：10 万文档批量导入 + 幂等键）。

幂等语义（审计 ARC-018）：dedup 键 = (kb_id, content_hash)——
同一文件可入多 KB，同 KB 内去重；重复导入返回既有文档，不产生新任务。
"""
import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.ingest.parsers import SUPPORTED_SUFFIXES
from core.storage.registry import Registry


@dataclass
class ImportStats:
    total: int = 0
    new: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def docs_per_hour(self) -> float:
        if self.elapsed_s <= 0:
            return 0.0
        return self.new / self.elapsed_s * 3600


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_directory(
    registry: Registry,
    kb_id: str,
    directory: str | Path,
    work_dir: Path,
    enqueue: Callable[[str], None],
) -> ImportStats:
    """扫描目录批量入队摄取任务链；同 KB 同内容幂等跳过。"""
    import time

    stats = ImportStats()
    start = time.perf_counter()
    root = Path(directory)
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    stats.total = len(files)
    for path in files:
        try:
            content_hash = _content_hash(path)
            if registry.find_document_by_hash(kb_id, content_hash) is not None:
                stats.skipped += 1
                continue
            doc = registry.create_document(
                kb_id=kb_id,
                title=path.name,
                source=str(path),
                content_hash=content_hash,
            )
            job = registry.create_job(doc.id, kb_id)
            job_dir = work_dir / job.id
            job_dir.mkdir(parents=True, exist_ok=True)
            # source 保留原始扩展名（管线按后缀路由解析器）
            shutil.copyfile(path, job_dir / f"source{path.suffix.lower()}")
            enqueue(job.id)
            stats.new += 1
        except Exception as exc:
            stats.failed += 1
            stats.errors.append(f"{path.name}: {exc}")
    stats.elapsed_s = time.perf_counter() - start
    return stats
