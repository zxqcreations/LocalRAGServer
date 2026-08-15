"""llama.cpp 生成实测（Spike 矩阵数据源）：Qwen3-8B Q4_K_M 的 token/s 与显存。

前置：先跑 download_llamacpp.py 与 download_gguf.py。
用法：uv run python scripts/spike/bench_llamacpp.py [--ngl 40] [--tokens 128]
"""
import argparse
import re
import subprocess  # nosec B404 -- 仅运行本地 llama-cli 基准（argv 全部来自仓库内常量与 argparse 数值）
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_LLAMACPP_DIR = ROOT / "models" / "llamacpp"
LLAMA_CLI = next(_LLAMACPP_DIR.rglob("llama-cli.exe"), _LLAMACPP_DIR / "llama-cli.exe")
GGUF = ROOT / "models" / "gguf" / "Qwen3-8B-Q4_K_M.gguf"

_TOKEN_RE = re.compile(r"eval time =.*\(\s*([\d.]+)\s*tokens per second\)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ngl", type=int, default=40, help="GPU offload 层数")
    parser.add_argument("--tokens", type=int, default=128, help="生成 token 数")
    args = parser.parse_args()

    for required, name in ((LLAMA_CLI, "llama-cli.exe"), (GGUF, "Qwen3-8B-Q4_K_M.gguf")):
        if not required.exists():
            print(f"缺少 {name}，请先运行 download_llamacpp.py / download_gguf.py")
            return 1

    cmd = [
        str(LLAMA_CLI),
        "-m", str(GGUF),
        "-p", "你好，请用一句话介绍量子计算。",
        "-n", str(args.tokens),
        "-ngl", str(args.ngl),
        "--no-display-prompt",
    ]
    print(f"运行：llama-cli -ngl {args.ngl} -n {args.tokens}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)  # nosec B603 -- argv 固定（llama-cli + 仓库内模型路径）
    output = result.stderr + result.stdout
    matches = _TOKEN_RE.findall(output)
    if matches:
        print(f"实测解码吞吐：{matches[-1]} tokens/s（ngl={args.ngl}）")
    else:
        print("未解析到吞吐数据，原始输出片段：")
        print(output[-2000:])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
