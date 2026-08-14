"""解析器单测：txt/md/pdf 与不支持格式。"""
import pymupdf
import pytest

from core.ingest.parsers import UnsupportedFormatError, parse_file


def test_parse_txt(tmp_path):
    p = tmp_path / "doc.txt"
    p.write_text("你好，世界。", encoding="utf-8")
    doc = parse_file(p)
    assert doc.title == "doc"
    assert doc.text == "你好，世界。"
    assert doc.pages is None


def test_parse_markdown_keeps_raw(tmp_path):
    # MVP 阶段 Markdown 原样返回（结构感知分块在 Phase 1+）
    p = tmp_path / "note.md"
    p.write_text("# 标题\n\n正文内容。", encoding="utf-8")
    doc = parse_file(p)
    assert "# 标题" in doc.text
    assert "正文内容。" in doc.text


def test_parse_pdf_two_pages(tmp_path):
    p = tmp_path / "paper.pdf"
    d = pymupdf.open()
    page = d.new_page()
    page.insert_text((72, 72), "Page One Content")
    page = d.new_page()
    page.insert_text((72, 72), "Page Two Content")
    d.save(str(p))
    d.close()

    doc = parse_file(p)
    assert "Page One Content" in doc.text
    assert "Page Two Content" in doc.text
    assert doc.pages == 2


def test_parse_pdf_preserves_page_breaks(tmp_path):
    # 页间用双换行分隔，保证分块时页边界可识别
    p = tmp_path / "two.pdf"
    d = pymupdf.open()
    for i in range(2):
        page = d.new_page()
        page.insert_text((72, 72), f"content of page {i + 1}")
    d.save(str(p))
    d.close()

    doc = parse_file(p)
    assert "\n\n" in doc.text


def test_unsupported_extension_raises(tmp_path):
    p = tmp_path / "data.docx"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(UnsupportedFormatError):
        parse_file(p)


def _make_minimal_epub(path) -> None:
    """构造最小合法 EPUB（zip 容器：mimetype + container.xml + 一个章节）。"""
    import zipfile

    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'unique-identifier="id" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Test Book</dc:title><dc:identifier id="id">test</dc:identifier>'
        "</metadata><manifest><item id=\"c1\" href=\"chap1.xhtml\" "
        'media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c1"/></spine></package>'
    )
    chapter = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ch1</title></head>'
        "<body><h1>第一章</h1><p>这是教材第一章的正文内容。</p></body></html>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/chap1.xhtml", chapter)


def test_parse_epub(tmp_path):
    p = tmp_path / "textbook.epub"
    _make_minimal_epub(p)
    doc = parse_file(p)
    assert doc.pages >= 1
    assert "第一章" in doc.text
    assert "教材" in doc.text


def test_epub_signature_check():
    from core.ingest.parsers import check_signature

    assert check_signature(".epub", b"PK\x03\x04rest")
    assert not check_signature(".epub", b"%PDF-1.7")
