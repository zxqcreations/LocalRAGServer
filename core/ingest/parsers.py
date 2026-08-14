"""文档解析：MVP 支持 txt/md/pdf（PyMuPDF）。

Office（docx/pptx/xlsx）、扫描件 OCR（PaddleOCR）与深度版面解析（MinerU）在 Phase 1 接入。
"""
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pymupdf  # PyMuPDF（新版模块名）

# 文本类（无魔数）；代码类 MVP 按纯文本摄取（tree-sitter 结构感知分块属 Phase 2）；
# epub 由 PyMuPDF 按文档解析（XHTML 页语义），与 pdf 同走 _parse_document
_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".html", ".htm"}
CODE_SUFFIXES = {
    ".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp", ".h",
    ".sh", ".toml", ".yaml", ".yml", ".json",
}
SUPPORTED_SUFFIXES = _TEXT_SUFFIXES | CODE_SUFFIXES | {".pdf", ".epub"}

# 文件头魔数（.txt/.md 无魔数，返回 True）；.epub 为 ZIP 容器
_MAGIC_HEADERS: dict[str, bytes] = {".pdf": b"%PDF", ".epub": b"PK\x03\x04"}


class UnsupportedFormatError(ValueError):
    """上传了暂不支持的文档格式。"""


class TooManyPagesError(ValueError):
    """PDF 页数超过上限（防解析器资源耗尽，审计 F-09）。"""


def check_signature(suffix: str, head: bytes) -> bool:
    """校验文件内容魔数与扩展名一致（不信任客户端声明的扩展名，审计 F-07）。"""
    magic = _MAGIC_HEADERS.get(suffix)
    return magic is None or head.startswith(magic)


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    text: str
    pages: int | None = None


def parse_file(path: str | Path, max_pages: int | None = None) -> ParsedDocument:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = sorted(SUPPORTED_SUFFIXES)
        raise UnsupportedFormatError(
            f"暂不支持格式 {suffix or '(无扩展名)'}：{p.name}（当前支持 {supported}）"
        )
    if suffix in {".pdf", ".epub"}:
        return _parse_document(p, max_pages)
    return ParsedDocument(title=p.stem, text=p.read_text(encoding="utf-8", errors="replace"))


def _parse_document(path: Path, max_pages: int | None) -> ParsedDocument:
    """PDF/EPUB 统一文档解析（PyMuPDF；epub 的页即 XHTML 章节页）。"""
    with pymupdf.open(path) as doc:
        if max_pages is not None and doc.page_count > max_pages:
            raise TooManyPagesError(
                f"文档页数超限：{doc.page_count} 页 > 上限 {max_pages} 页"
            )
        # get_text() 无参数时恒返回 str（typeshed 声明过宽，显式收窄）
        pages = [cast(str, page.get_text()) for page in doc]
    # 页间保留双换行，使页边界对分块器可见
    text = "\n\n".join(page for page in pages if page.strip())
    return ParsedDocument(title=path.stem, text=text, pages=len(pages))
