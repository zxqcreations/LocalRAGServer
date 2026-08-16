"""旧模型清理（release-rollback.md 登记项：旧模型保留 ≥72h 契约的执行侧）。

扫描 models/gguf 下未被 MANIFEST.json 引用且 mtime 超过保留期（默认 72h）的
模型文件，列出候选；--apply 执行删除。

用法：
  uv run python scripts/spike/prune_old_models.py            # 只列出候选
  uv run python scripts/spike/prune_old_models.py --apply    # 执行删除
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GGUF_DIR = ROOT / "models" / "gguf"
MANIFEST = ROOT / "models" / "MANIFEST.json"
RETENTION_HOURS = 72  # 旧模型保留 ≥72h 契约（release-rollback.md）


def _referenced_names() -> set[str]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {e["name"] for e in data.get("entries", [])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="执行删除")
    parser.add_argument("--retention-hours", type=float, default=RETENTION_HOURS)
    args = parser.parse_args()

    if not GGUF_DIR.is_dir():
        print(f"模型目录不存在：{GGUF_DIR}")
        return 1
    referenced = _referenced_names()
    now = time.time()
    candidates: list[Path] = []
    for f in GGUF_DIR.glob("*.gguf"):
        if f.name in referenced:
            continue
        age_h = (now - f.stat().st_mtime) / 3600
        if age_h >= args.retention_hours:
            candidates.append(f)
            print(f"候选（{age_h:.0f}h 未引用）：{f.name}（{f.stat().st_size // 1024 // 1024}MB）")
    if not candidates:
        print("无过期未引用模型")
        return 0
    if args.apply:
        for f in candidates:
            f.unlink()
            print(f"已删除：{f.name}")
    else:
        print(f"\n共 {len(candidates)} 个候选，加 --apply 执行删除")
    return 0


if __name__ == "__main__":
    sys.exit(main())
