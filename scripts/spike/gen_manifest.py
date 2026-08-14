"""生成模型资产 MANIFEST（审计 F-17：名称/版本/sha256/来源 URL，可复现性）。

用法：uv run python scripts/spike/gen_manifest.py
"""
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "models" / "MANIFEST.json"

ENTRIES = [
    {
        "name": "Qwen3-8B-Q4_K_M.gguf",
        "path": "models/gguf/Qwen3-8B-Q4_K_M.gguf",
        "source_url": "https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
        "version": "Q4_K_M（unsloth 仓库 main 分支，2026-08 下载）",
    },
    {
        "name": "llama.cpp",
        "path": "models/llamacpp/",
        "source_url": "https://github.com/ggml-org/llama.cpp/releases/latest",
        "version": "b10427（commit 650913862，CUDA 12.4 win-x64，钉版本）",
    },
    {
        "name": "BAAI/bge-m3",
        "path": str(
            Path.home() / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-m3"
        ),
        "source_url": "https://huggingface.co/BAAI/bge-m3",
        "version": "HF 缓存（huggingface_hub 管理，snapshot 见缓存目录）",
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    entries: list[dict[str, object]] = []
    for e in ENTRIES:
        p = ROOT / e["path"]
        entry: dict[str, object] = {k: v for k, v in e.items() if k != "path"}
        if p.is_file():
            start = time.perf_counter()
            entry["sha256"] = sha256(p)
            entry["size_bytes"] = p.stat().st_size
            print(f"{e['name']}: sha256 完成（{time.perf_counter() - start:.1f}s）")
        elif p.is_dir():
            entry["present"] = any(p.iterdir())
        else:
            entry["present"] = False
        entries.append(entry)
    payload = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "entries": entries}
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"MANIFEST 已写入：{MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
