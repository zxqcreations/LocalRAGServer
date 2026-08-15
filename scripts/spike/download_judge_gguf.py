"""下载独立评判模型 Qwen2.5-7B-Instruct Q4_K_M（约 4.7GB），断点续传。

judge 独立选型（quality.md Phase 5 关键门 ①）：与生成模型（Qwen3-8B）解耦，
消除自评偏置。本机 11GB 显存不足以同时容纳两个模型 → 评判模型跑 CPU
（llama-server 无 -ngl，端口 9002）。

urllib 自动读取系统注册表代理。下载不设超时。
用法：uv run python scripts/spike/download_judge_gguf.py
"""
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# 仓库内 q4_k_m 为两分片（-00001/00002-of-00002）
BASE = (
    "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/"
    "qwen2.5-7b-instruct-q4_k_m-{part}-of-00002.gguf"
)
DEST_DIR = Path(__file__).resolve().parents[2] / "models" / "gguf"
UA = {"User-Agent": "LocalRAGServer-spike"}


def _download(url: str, dest: Path) -> None:
    if urlparse(url).scheme not in {"https", "http"}:
        raise ValueError(f"仅允许 http(s) 下载：{url}")
    existing = dest.stat().st_size if dest.exists() else 0
    req = urllib.request.Request(url, headers=UA)
    if existing:
        req.add_header("Range", f"bytes={existing}-")
    with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 -- scheme 白名单已校验
        total = int(resp.headers.get("Content-Length") or 0)
        print(
            f"{dest.name}：已存在 {existing // 1024 // 1024}MB，"
            f"追加 {total // 1024 // 1024}MB ..."
        )
        mode = "ab" if resp.status == 206 else "wb"
        with dest.open(mode) as f:
            while chunk := resp.read(1024 * 1024):
                f.write(chunk)
    print(f"完成：{dest.name}（{dest.stat().st_size // 1024 // 1024}MB）")


def main() -> int:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    for part in ("00001", "00002"):
        dest = DEST_DIR / f"qwen2.5-7b-instruct-q4_k_m-{part}-of-00002.gguf"
        _download(BASE.format(part=part), dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
