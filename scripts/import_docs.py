"""批量导入 CLI（幂等键 (kb_id, content_hash)；需要 worker 消费任务链）。

用法：
  uv run python scripts/import_docs.py --kb <kb名称或id> --dir <目录>
  uv run python scripts/worker.py   # 另开终端消费任务
"""
import argparse
import sys

from core.config import get_settings
from core.ingest.importer import import_directory
from core.ingest.tasks import enqueue_ingest
from core.storage.registry import Registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb", required=True, help="知识库名称或 id")
    parser.add_argument("--dir", required=True, help="文档目录（递归扫描）")
    args = parser.parse_args()

    settings = get_settings()
    if settings.database_url is None:  # validator 已派生，fail-fast 兜底
        raise RuntimeError("database_url 未配置")
    registry = Registry(settings.database_url)
    kb = registry.get_kb(args.kb)
    if kb is None:
        kb = next((k for k in registry.list_kbs() if k.name == args.kb), None)
    if kb is None:
        print(f"知识库不存在：{args.kb}")
        return 1

    stats = import_directory(
        registry,
        kb.id,
        args.dir,
        settings.data_dir / "ingest_work",
        enqueue_ingest,
    )
    print(
        f"扫描 {stats.total} 篇 · 新入队 {stats.new} · 幂等跳过 {stats.skipped} · "
        f"失败 {stats.failed}"
    )
    print(f"耗时 {stats.elapsed_s:.1f}s · 入队速率 {stats.docs_per_hour:.0f} 篇/小时")
    for err in stats.errors[:5]:
        print(f"  [失败] {err}")
    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
