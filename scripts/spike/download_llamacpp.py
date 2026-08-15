"""下载并解压 llama.cpp Windows CUDA 构建（钉版本，写入兼容矩阵）。

urllib 自动读取系统注册表代理（本机 7897）。
用法：uv run python scripts/spike/download_llamacpp.py
"""
import json
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
DEST = Path(__file__).resolve().parents[2] / "models" / "llamacpp"
UA = {"User-Agent": "LocalRAGServer-spike"}


def fetch(url: str):
    """大文件下载不设总超时（仅连接超时），断点由调用方处理。"""
    # 资产 URL 来自 GitHub API 响应，白名单校验防 file:// 等非预期方案
    if urlparse(url).scheme not in {"https", "http"}:
        raise ValueError(f"仅允许 http(s) 下载：{url}")
    return urllib.request.urlopen(  # nosec B310 -- scheme 白名单已校验
        urllib.request.Request(url, headers=UA), timeout=120
    )


def main() -> int:
    info = json.loads(fetch(API).read())
    tag = info["tag_name"]
    assets = [
        a
        for a in info["assets"]
        if "bin-win-cuda-" in a["name"]
        and a["name"].endswith("x64.zip")
        and not a["name"].startswith("cudart")  # cudart 变体捆绑 CUDA 运行时，取纯净版
    ]
    if not assets:
        print("未找到 bin-win-cuda x64 资产，请检查 releases 页面")
        return 1
    # 优先 CUDA 12.4 构建：Turing（SM75）驱动兼容性最稳；13.3 需驱动 ≥580
    asset = next((a for a in assets if "12.4" in a["name"]), assets[0])
    DEST.mkdir(parents=True, exist_ok=True)
    zip_path = DEST / asset["name"]
    if not zip_path.exists() or zip_path.stat().st_size < asset["size"]:
        print(f"下载 {asset['name']}（{asset['size'] // 1024 // 1024}MB）...")
        with fetch(asset["browser_download_url"]) as resp, zip_path.open("wb") as f:
            while chunk := resp.read(1024 * 1024):
                f.write(chunk)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(DEST)
    exe = next(DEST.rglob("llama-cli.exe"), None)  # 兼容可能的子目录布局
    if exe is None:
        print("解压后未找到 llama-cli.exe")
        return 1
    print(f"llama.cpp {tag} 就绪：{exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
