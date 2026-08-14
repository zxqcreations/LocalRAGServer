"""GPU 环境守卫测试：防止 uv sync 用 PyPI cpu 版 torch 覆写 GPU 版（已发生多次的环境坑）。"""
import shutil

import pytest


def test_gpu_torch_not_clobbered():
    if shutil.which("nvidia-smi") is None:
        pytest.skip("无 GPU 环境（CI）")
    torch = pytest.importorskip("torch")
    assert torch.cuda.is_available(), (
        "GPU torch 被覆写为 CPU 版。恢复命令："
        "uv pip install torch==2.9.1 --index https://download.pytorch.org/whl/cu126"
    )
