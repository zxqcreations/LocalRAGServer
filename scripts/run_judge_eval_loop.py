"""独立评判评测自动续跑循环（Phase 5 关键门 ① 实测编排）。

幂等设计：offset 由已落盘明细条数决定，被外部终止后重启脚本即续跑；
每批 10 条（CPU 评判 ~10 分钟/批，适配执行窗口）；生成（9001 GPU）与
评判（9002 CPU）服务按需拉起。

用法：uv run python scripts/run_judge_eval_loop.py [--total 50] [--batch 10]
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERF = ROOT / "docs" / "perf"
# judge 独立重跑：首次运行清掉旧同源口径的批次明细（新口径新明细，
# 基线整体更新）；标记文件保证只清一次——重启续跑不丢已完成明细
MARKER = PERF / ".judge-v2-marker"
if not MARKER.exists():
    for old in PERF.glob("ragas-eval-*.jsonl"):
        if "full" not in old.name:
            old.unlink(missing_ok=True)
    MARKER.write_text("independent-judge v2", encoding="utf-8")


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def _start_server(args: list[str], log: Path) -> subprocess.Popen:
    log.parent.mkdir(parents=True, exist_ok=True)
    f = log.open("ab")
    return subprocess.Popen(  # nosec B603 -- 仓库内固定 argv（llama-server + 本地模型路径）
        args, stdout=f, stderr=subprocess.STDOUT, cwd=ROOT
    )


def _wait_healthy(port: int, timeout_s: int = 300) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _healthy(port):
            return True
        time.sleep(3)
    return False


def _done_count() -> int:
    """已完成条目数：合并所有批次明细的去重 id 数（ragas_runner 按
    ragas-eval-<offset>-<ts>.jsonl 命名，逐条落盘）。"""
    seen: set[str] = set()
    for f in PERF.glob("ragas-eval-*.jsonl"):
        if "full" in f.name:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                continue
    return len(seen)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=50)
    parser.add_argument("--batch", type=int, default=10)
    args = parser.parse_args()

    gen_args = [
        str(ROOT / "models" / "llamacpp" / "llama-server.exe"),
        "-m", str(ROOT / "models" / "gguf" / "Qwen3-8B-Q4_K_M.gguf"),
        "--host", "127.0.0.1", "--port", "9001", "-ngl", "40", "-c", "8192",
    ]
    judge_args = [
        str(ROOT / "models" / "llamacpp" / "llama-server.exe"),
        "-m", str(ROOT / "models" / "gguf" / "qwen2.5-7b-instruct-q4_k_m.gguf"),
        "--host", "127.0.0.1", "--port", "9002", "-c", "8192",
    ]
    gen_proc: subprocess.Popen | None = None
    # judge（7B CPU）按批启停：常驻 ~10GB RAM 与 bge-m3 加载互相挤压
    # （32GB 机器实测：常驻时 bge-m3 CPU 加载被换页拖死）。生成端（8B GPU）
    # 常驻 OK——检索/生成每批都需要。
    judge_proc: subprocess.Popen | None = None

    def ensure_gen() -> None:
        nonlocal gen_proc
        if not _healthy(9001):
            gen_proc = _start_server(gen_args, ROOT / "data" / "llama-server.log")
        if not _wait_healthy(9001):
            raise RuntimeError("生成 llama-server 启动超时")

    def start_judge() -> None:
        nonlocal judge_proc
        if not _healthy(9002):
            judge_proc = _start_server(judge_args, ROOT / "data" / "llama-judge.log")
        if not _wait_healthy(9002, timeout_s=600):
            raise RuntimeError("评判 llama-server 启动超时")

    def stop_judge() -> None:
        nonlocal judge_proc
        if judge_proc is not None:
            judge_proc.terminate()
            judge_proc.wait(timeout=30)
            judge_proc = None

    try:
        while True:
            done = _done_count()
            if done >= args.total:
                print(f"全部完成：{done}/{args.total}")
                return 0
            ensure_gen()
            start_judge()
            batch_end = min(done + args.batch, args.total)
            env = {
                **__import__("os").environ,
                "RAGAS_JUDGE_BASE_URL": "http://127.0.0.1:9002/v1",
                "RAGAS_JUDGE_MODEL": "qwen2.5-7b-instruct",
            }
            print(f"批次：offset={done} limit={batch_end - done}")
            result = subprocess.run(  # nosec B603 -- 仓库内固定 argv
                ["uv", "run", "python", "-m", "eval.ragas_runner", "--offset", str(done),
                 "--limit", str(batch_end - done), "--skip-errors"],
                cwd=ROOT, env=env,
            )
            stop_judge()  # 释放 RAM（bge-m3 下一批加载需要）
            if result.returncode != 0:
                print(f"批次失败（退出码 {result.returncode}），重试")
    finally:
        stop_judge()
        if gen_proc is not None:
            gen_proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
