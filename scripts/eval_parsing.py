"""真实数据解析评估（Phase 1 解析质量抽检，quality.md 门禁：每解析器类型抽样 ≥5 篇）。

数据源（用户提供）：
- PDF/EPUB：D:/下载的文献/WaveguideAmp（63 PDF + 2 EPUB）
- MD：D:/ENV/claude/Photonic-Amplifier/research（100 篇）

对每类抽样（按大小秩确定性选样）：pymupdf 文本层解析 + MinerU 深度解析（PDF），
记录耗时/页数/文本量/文本层有无，输出报告到 docs/perf/parsing-eval-<date>.md。

用法：uv run python scripts/eval_parsing.py [--sample 5]
"""
import argparse
import subprocess  # nosec B404 -- 仅运行本地 mineru CLI（固定 argv，路径来自本地抽样文件）
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.ingest.parsers import parse_file

PDF_ROOT = Path("D:/下载的文献/WaveguideAmp")
MD_ROOT = Path("D:/ENV/claude/Photonic-Amplifier/research")
OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "perf"


@dataclass
class Result:
    name: str
    kind: str
    size_mb: float
    time_s: float
    pages: int = 0
    chars: int = 0
    text_layer: str = "-"
    mineru_time_s: float | None = None
    mineru_out_mb: float | None = None
    mineru_note: str = ""
    chunks: int = 0
    notes: list[str] = field(default_factory=list)


def _sample_by_size(files: list[Path], n: int) -> list[Path]:
    """确定性抽样：最小、最大 + n-2 个中间（按大小秩均匀取）。"""
    ordered = sorted(files, key=lambda p: p.stat().st_size)
    if len(ordered) <= n:
        return ordered
    picks = {0, len(ordered) - 1}
    step = len(ordered) / (n - 1)
    picks |= {min(len(ordered) - 1, int(i * step)) for i in range(n)}
    return [ordered[i] for i in sorted(picks)]


def _run_mineru(pdf: Path, out_root: Path) -> tuple[float, float, str]:
    start = time.perf_counter()
    result = subprocess.run(  # nosec B603 B607 -- mineru 为本地解析工具，argv 不含外部输入
        ["mineru", "-p", str(pdf), "-o", str(out_root)],
        capture_output=True,
        text=True,
        timeout=1800,  # 大 PDF 深度解析耗时长，不设过短超时
    )
    elapsed = time.perf_counter() - start
    md_files = list(out_root.rglob("*.md"))
    size = sum(f.stat().st_size for f in md_files) / 1024 / 1024 if md_files else 0.0
    note = ""
    if result.returncode != 0:
        note = f"mineru 失败: {(result.stderr or '')[-200:]}"
    return elapsed, size, note


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=5)
    parser.add_argument(
        "--mineru", action="store_true", help="运行 MinerU 深度解析（较慢，首次需下载模型）"
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []

    # ---- PDF（文献） ----
    pdfs = sorted(PDF_ROOT.rglob("*.pdf")) if PDF_ROOT.is_dir() else []
    for pdf in _sample_by_size(pdfs, args.sample):
        r = Result(pdf.name, "pdf", pdf.stat().st_size / 1024 / 1024, 0.0)
        start = time.perf_counter()
        try:
            doc = parse_file(pdf, max_pages=2000)
            r.time_s = time.perf_counter() - start
            r.pages = doc.pages or 0
            r.chars = len(doc.text)
            r.text_layer = "有" if doc.text.strip() else "无"
            if r.pages and r.chars / max(r.pages, 1) < 50:
                r.notes.append("疑似扫描件（平均每页文本 <50 字符），需 OCR")
            if args.mineru:
                out_root = OUT_DIR / "mineru_out" / pdf.stem
                out_root.mkdir(parents=True, exist_ok=True)
                try:
                    r.mineru_time_s, r.mineru_out_mb, r.mineru_note = _run_mineru(pdf, out_root)
                except subprocess.TimeoutExpired:
                    r.mineru_note = "mineru 超时（>30min）"
        except Exception as exc:
            r.notes.append(f"解析失败: {exc}")
        results.append(r)

    # ---- EPUB（教材） ----
    epubs = sorted(PDF_ROOT.rglob("*.epub")) if PDF_ROOT.is_dir() else []
    for epub in _sample_by_size(epubs, 2):
        r = Result(epub.name, "epub", epub.stat().st_size / 1024 / 1024, 0.0)
        start = time.perf_counter()
        try:
            doc = parse_file(epub, max_pages=2000)
            r.time_s = time.perf_counter() - start
            r.pages = doc.pages or 0
            r.chars = len(doc.text)
            r.text_layer = "有" if doc.text.strip() else "无"
        except Exception as exc:
            r.notes.append(f"解析失败: {exc}")
        results.append(r)

    # ---- MD（领域知识库） ----
    mds = sorted(MD_ROOT.rglob("*.md")) if MD_ROOT.is_dir() else []
    for md in _sample_by_size(mds, args.sample):
        r = Result(md.name, "md", md.stat().st_size / 1024 / 1024, 0.0)
        start = time.perf_counter()
        try:
            doc = parse_file(md)
            from core.ingest.chunker import chunk_text

            chunks = chunk_text(doc.text)
            r.time_s = time.perf_counter() - start
            r.chars = len(doc.text)
            r.chunks = len(chunks)
        except Exception as exc:
            r.notes.append(f"解析失败: {exc}")
        results.append(r)

    # ---- 报告 ----
    lines = [
        "# 解析质量抽检报告（真实数据）",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')} · 抽样：每类 {args.sample} 篇",
        "",
    ]
    lines.append(
        "| 文件 | 类型 | 大小MB | 耗时s | 页数 | 字符 | 文本层 | chunks "
        "| MinerU s | MinerU MB | 备注 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        mineru_s = f"{r.mineru_time_s:.1f}" if r.mineru_time_s is not None else "-"
        mineru_mb = f"{r.mineru_out_mb:.2f}" if r.mineru_out_mb is not None else "-"
        note = "；".join(r.notes + ([r.mineru_note] if r.mineru_note else []))
        lines.append(
            f"| {r.name[:48]} | {r.kind} | {r.size_mb:.1f} | {r.time_s:.2f} | {r.pages} "
            f"| {r.chars} | {r.text_layer} | {r.chunks} | {mineru_s} | {mineru_mb} | {note} |"
        )
    report = OUT_DIR / f"parsing-eval-{time.strftime('%Y%m%d')}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告已写入：{report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
