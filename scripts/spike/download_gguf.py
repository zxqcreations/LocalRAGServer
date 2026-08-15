"""下载 Qwen3-8B Q4_K_M GGUF（约 5.2GB），支持断点续传（Range）。

urllib 自动读取系统注册表代理（本机 7897）。下载不设超时。
用法：uv run python scripts/spike/download_gguf.py
"""
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

URL = "https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf"
DEST = Path(__file__).resolve().parents[2] / "models" / "gguf" / "Qwen3-8B-Q4_K_M.gguf"
UA = {"User-Agent": "LocalRAGServer-spike"}


def main() -> int:
    if urlparse(URL).scheme not in {"https", "http"}:
        raise ValueError(f"仅允许 http(s) 下载：{URL}")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    existing = DEST.stat().st_size if DEST.exists() else 0
    req = urllib.request.Request(URL, headers=UA)
    if existing:
        req.add_header("Range", f"bytes={existing}-")
    with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 -- scheme 白名单已校验
        total = int(resp.headers.get("Content-Length") or 0)
        print(f"已存在 {existing // 1024 // 1024}MB，本次追加 {total // 1024 // 1024}MB ...")
        # 服务器不支持 Range 返回 200 全量时，重写文件防止损坏
        mode = "ab" if resp.status == 206 else "wb"
        with DEST.open(mode) as f:
            while chunk := resp.read(1024 * 1024):
                f.write(chunk)
    print(f"完成：{DEST}（{DEST.stat().st_size // 1024 // 1024}MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
