"""摄取 worker 启动入口（ADR-002：Windows 原生进程；--pool=solo 为 Windows 兼容形态）。

用法：
  uv run python scripts/worker.py                      # 仅消费摄取任务
  uv run python scripts/worker.py --beat               # 同时启动 beat（URL 订阅爬取调度，
                                                       # crawl.due 每 10 分钟扫描到期订阅）

或直接：
  uv run celery -A core.ingest.tasks worker --pool=solo --loglevel=info [-B]
"""
import sys

from core.ingest.tasks import app

if __name__ == "__main__":
    argv = ["-A", "core.ingest.tasks", "worker", "--pool=solo", "--loglevel=info"]
    if "--beat" in sys.argv:
        argv.append("-B")
    app.worker_main(argv)
