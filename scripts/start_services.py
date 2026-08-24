# -*- coding: utf-8 -*-
"""Start LocalRAGServer services (Windows-compatible).

Usage:
    python scripts/start_services.py      # Start all services
    python scripts/start_services.py --worker-only   # Worker only (recommended)
    python scripts/start_services.py --beat          # Beat scheduler only

On Windows, worker and beat must run as separate processes because
Celery beat requires native subprocess support not available in --pool=solo mode.
"""
import os
import sys
import subprocess


def start_worker():
    """Start ingestion worker with solo pool (Windows compatible)."""
    print("[1/2] Starting ingestion worker...")
    cmd = [
        sys.executable, "-m", "celery",
        "-A", "core.ingest.tasks",
        "worker", "--pool=solo", "--loglevel=info"
    ]
    print(f"  Running: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=os.path.dirname(os.path.dirname(__file__)))
    print(f"  PID: {proc.pid}")
    return proc


def start_beat():
    """Start beat scheduler separately (Windows requires standalone process)."""
    print("\n[2/2] Starting beat scheduler...")
    cmd = [
        sys.executable, "-m", "celery",
        "-A", "core.ingest.tasks",
        "beat", "--loglevel=info",
        "--scheduler", "celery.beat:PersistentScheduler"
    ]
    print(f"  Running: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=os.path.dirname(os.path.dirname(__file__)))
    print(f"  PID: {proc.pid}")
    print("  NOTE: Beat scans crawl.due every 10 minutes for URL subscriptions.")
    return proc


def start_server():
    """Start uvicorn FastAPI server."""
    print("\n[3/3] Starting API server...")
    cmd = [
        sys.executable, "-m", "uvicorn",
        "apps.api.main:create_app",
        "--factory",
        "--host", "127.0.0.1",
        "--port", "8000"
    ]
    print(f"  Running: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=os.path.dirname(os.path.dirname(__file__)))
    print(f"  PID: {proc.pid}")
    print("  Server URL: http://127.0.0.1:8000")
    return proc


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Start LocalRAGServer services")
    ap.add_argument("--worker-only", action="store_true", help="Only start worker")
    ap.add_argument("--beat", action="store_true", help="Only start beat scheduler")
    ap.add_argument("--server", action="store_true", help="Only start API server")

    args = ap.parse_args()

    if args.worker_only:
        start_worker()
    elif args.beat:
        start_beat()
    elif args.server:
        start_server()
    else:
        # Start all
        w = start_worker()
        try:
            b = start_beat()
        except Exception as e:
            print(f"\n[!] Beat failed: {e}")
            print("    This is normal on Windows — beat requires native subprocess support.")
            print("    Workers will still handle document ingestion.")
            print("    To enable URL subscription scheduling:")
            print("      1. Run beat separately when needed")
            print("      2. Or use Linux subsystem / Docker Desktop")
        s = start_server()

        print("\n" + "=" * 60)
        print("Services started:")
        print(f"  - Worker (PID {w.pid})")
        try:
            print(f"  - Beat (PID {b.pid})")
        except UnboundLocalError:
            print("  - Beat (not started due to Windows limitation)")
        print(f"  - Server (PID {s.pid}) -> http://127.0.0.1:8000")
        print("=" * 60)

        # Keep alive
        try:
            w.wait()
            b.wait() if 'b' in locals() else None
            s.wait()
        except KeyboardInterrupt:
            print("\nShutting down...")
            for p in [p for p in [w, s] if hasattr(p, 'pid')]:
                try:
                    p.terminate()
                    p.wait(timeout=5)
                except:
                    pass
