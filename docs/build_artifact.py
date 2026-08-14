"""构建 docs/architecture-artifact.html：将字体以 data URI 内联进模板。

字体获取顺序：Google Fonts css2 API（woff2）→ jsdelivr GitHub 镜像（ttf）→ 放弃内联。
任一步失败均降级，模板中的 @font-face 会被替换为 src:none，浏览器回退系统字体栈。
"""
import base64
import re
import urllib.request
from pathlib import Path

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}

# 占位符 -> (css2 家族名, 字重)
FONTS = {
    "__FONT_MONO_400__": ("JetBrains+Mono", 400),
    "__FONT_MONO_700__": ("JetBrains+Mono", 700),
    "__FONT_SANS_400__": ("IBM+Plex+Sans", 400),
    "__FONT_SANS_600__": ("IBM+Plex+Sans", 600),
}

# jsdelivr 兜底（TTF 较大，仅作第二选择）
GH_TTF = {
    "JetBrains+Mono": (
        "https://cdn.jsdelivr.net/gh/JetBrains/JetBrainsMono@master/"
        "fonts/ttf/JetBrainsMono-{weight}.ttf"
    ),
    "IBM+Plex+Sans": (
        "https://cdn.jsdelivr.net/gh/IBM/plex@master/IBM-Plex-Sans/"
        "fonts/complete/ttf/IBMPlexSans-{weight}.ttf"
    ),
}
WEIGHT_NAME = {400: "Regular", 600: "SemiBold", 700: "Bold"}


def fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def from_css2(family: str, weight: int):
    css = fetch(
        f"https://fonts.googleapis.com/css2?family={family}:wght@{weight}&display=swap"
    ).decode("utf-8", "replace")
    # 只取 latin 子集块
    blocks = re.split(r"/\*\s*(\S+)\s*\*/", css)
    latin_css = css
    for i in range(1, len(blocks), 2):
        if blocks[i] == "latin":
            latin_css = blocks[i + 1]
            break
    match = re.search(r"url\((https://[^)]+\.woff2)\)", latin_css)
    if not match:
        raise RuntimeError("css2 中未找到 woff2 地址")
    data = fetch(match.group(1), timeout=30)
    return "woff2", base64.b64encode(data).decode("ascii")


def from_github(family: str, weight: int):
    url = GH_TTF[family].format(weight=WEIGHT_NAME[weight])
    data = fetch(url, timeout=30)
    return "truetype", base64.b64encode(data).decode("ascii")


def main() -> None:
    root = Path(__file__).resolve().parent
    template = (root / "artifact-template.html").read_text(encoding="utf-8")
    out = template
    for ph, (family, weight) in FONTS.items():
        pattern = re.compile(r'src:url\("' + ph + r'"\) format\("woff2"\)')
        fmt, data = None, None
        for label, getter in (("css2", from_css2), ("jsdelivr", from_github)):
            try:
                fmt, data = getter(family, weight)
                print(f"{ph}: OK via {label} ({len(data) // 1024}KB base64)")
                break
            except Exception as exc:  # 网络或解析失败均降级
                print(f"{ph}: {label} 失败 ({exc.__class__.__name__}: {exc})")
        if fmt and data:
            src = f'src:url("data:font/{fmt};base64,{data}") format("{fmt}")'
        else:
            src = "src:none"
            print(f"{ph}: 内联失败，回退系统字体栈")
        out, count = pattern.subn(src, out)
        if count != 1:
            raise SystemExit(f"{ph}: 模板中占位符匹配数异常 ({count})")
    target = root / "architecture-artifact.html"
    target.write_text(out, encoding="utf-8")
    print(f"已生成 {target} ({target.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()
