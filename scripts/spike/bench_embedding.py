"""bge-m3 实测（Spike 矩阵数据源）：设备、显存、批量吞吐。

首次运行会自动下载 bge-m3 权重（~2.2GB，huggingface_hub 走环境代理——
需以 HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897 环境启动）。
用法：uv run python scripts/spike/bench_embedding.py
"""
import sys
import time


def main() -> int:
    import torch
    from sentence_transformers import SentenceTransformer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"torch {torch.__version__} · CUDA {torch.version.cuda} · device={device}")
    free_before = 0
    total = 0
    if device == "cuda":
        name = torch.cuda.get_device_name(0)
        free_before, total = torch.cuda.mem_get_info()
        print(f"GPU: {name} · VRAM {total // 1024**2}MB / 空闲 {free_before // 1024**2}MB")

    model = SentenceTransformer("BAAI/bge-m3", device=device)
    dim = model.get_embedding_dimension()
    print(f"bge-m3 加载完成 · dim={dim}")

    if device == "cuda":
        free_after = torch.cuda.mem_get_info()[0]
        used = (free_before - free_after) // 1024**2
        print(f"模型驻留显存 ≈ {used}MB")

    # 批量嵌入吞吐：200 字符级文本 × 320 条，batch 16
    texts = [
        f"量子计算使用量子比特与叠加态实现并行计算，这是第 {i} 条测试文本。" * 4
        for i in range(320)
    ]
    model.encode(texts[:16])  # 预热
    start = time.perf_counter()
    for i in range(0, len(texts), 16):
        model.encode(texts[i : i + 16], normalize_embeddings=True)
    elapsed = time.perf_counter() - start
    print(f"批量嵌入：{len(texts)} 条 / {elapsed:.1f}s = {len(texts) / elapsed:.0f} 条/s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
