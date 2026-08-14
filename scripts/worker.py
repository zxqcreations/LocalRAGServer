"""摄取 worker 启动入口（ADR-002：Windows 原生进程；--pool=solo 为 Windows 兼容形态）。

用法：uv run python scripts/worker.py
或直接：uv run celery -A core.ingest.tasks worker --pool=solo --loglevel=info
"""
from core.ingest.tasks import app

if __name__ == "__main__":
    app.worker_main(["-A", "core.ingest.tasks", "worker", "--pool=solo", "--loglevel=info"])
