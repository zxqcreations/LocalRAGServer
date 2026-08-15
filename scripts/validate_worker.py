"""真实 worker 进程全链路验证（ADR-002 实机验证：filesystem 代理 + 独立 worker 进程）。

流程：启动 worker 子进程 → 建 KB/文档/任务 + 源文件 → 入队任务链 →
轮询 ingest_jobs 直到 ready/failed（上限 180s）→ 输出结果并终止 worker。

用法：uv run python scripts/validate_worker.py
"""
import subprocess  # nosec B404 -- 仅启动仓库内 worker 脚本（sys.executable + 固定路径，无外部输入）
import sys
import time
from pathlib import Path

from core.config import get_settings
from core.ingest.state import Stage
from core.ingest.tasks import enqueue_ingest
from core.storage.registry import Registry


def main() -> int:
    settings = get_settings()
    if settings.database_url is None:  # validator 已派生，fail-fast 兜底
        raise RuntimeError("database_url 未配置")
    registry = Registry(settings.database_url)

    log_path = Path("data") / "worker_validate.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8", errors="replace")
    worker = subprocess.Popen(  # nosec B603 -- argv 固定（sys.executable + 仓库脚本路径）
        [sys.executable, "scripts/worker.py"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(12)  # 等 worker 连接代理并就绪
        kb = registry.create_kb("worker验证")
        doc = registry.create_document(kb.id, "w.md", "validate://worker", "hash-w")
        job = registry.create_job(doc.id, kb.id)
        work = settings.data_dir / "ingest_work" / job.id
        work.mkdir(parents=True, exist_ok=True)
        (work / "source.md").write_text(
            "# 验证\n\nworker 进程真实链路：量子计算使用量子比特。", encoding="utf-8"
        )
        enqueue_ingest(job.id)

        deadline = time.time() + 180
        while time.time() < deadline:
            fresh = registry.get_job(job.id)
            if fresh is not None and fresh.stage in {Stage.READY.value, Stage.FAILED.value}:
                break
            time.sleep(2)
        fresh = registry.get_job(job.id)
        if fresh is None or fresh.stage != Stage.READY.value:
            stage = fresh.stage if fresh else "无任务"
            error = fresh.error if fresh else ""
            print(f"worker 验证失败：stage={stage} error={error}")
            print("---- worker 日志尾部 ----")
            print(log_path.read_text(encoding="utf-8", errors="replace")[-1500:])
            return 1
        doc_fresh = registry.get_document(kb.id, doc.id)
        chunks = doc_fresh.chunk_count if doc_fresh is not None else 0
        print(
            f"worker 验证通过：job 状态 ready · chunk={chunks} · "
            f"attempt={fresh.attempt}"
        )
        return 0
    finally:
        worker.terminate()
        worker.wait(timeout=30)
        registry.close()


if __name__ == "__main__":
    sys.exit(main())
