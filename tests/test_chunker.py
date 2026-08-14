"""分块器单测：边界、重叠、偏移、硬切、异常。"""
from itertools import pairwise

import pytest

from core.ingest.chunker import chunk_text

SAMPLE = "第一段内容。\n\n第二段内容，用于测试分块。\n第三段。" * 20


def test_chunks_bounded_by_size():
    # 常规片段合并的 chunk 不超过 chunk_size + overlap（尾部重叠残留）
    chunks = chunk_text(SAMPLE, chunk_size=200, overlap=40)
    assert chunks
    assert all(len(c.text) <= 200 + 40 for c in chunks)


def test_adjacent_chunks_overlap_by_tail():
    # 相邻 chunk 尾部与头部重叠：chunk[i].text[-overlap:] == chunk[i+1].text[:overlap]
    chunks = chunk_text(SAMPLE, chunk_size=200, overlap=40)
    assert len(chunks) > 1
    for prev, nxt in pairwise(chunks):
        assert prev.text[-40:] == nxt.text[:40]


def test_offsets_ordered_and_complete():
    # 偏移单调递增，首块从 0 开始；
    # 不变式：所有非分隔符字符必须被覆盖（末尾分隔符按设计丢弃，不产生内容块）
    chunks = chunk_text(SAMPLE, chunk_size=200, overlap=40)
    starts = [c.start for c in chunks]
    assert starts == sorted(starts)
    assert chunks[0].start == 0
    tail = SAMPLE
    while tail and tail[-1] in "。\n":
        tail = tail[:-1]
    assert chunks[-1].end == len(tail)
    assert all(c.end <= len(SAMPLE) for c in chunks)


def test_long_unbreakable_piece_hard_split():
    # 无分隔符的超长文本按 chunk_size 硬切
    text = "x" * 2000
    chunks = chunk_text(text, chunk_size=512, overlap=64)
    assert len(chunks) >= 4
    assert all(len(c.text) <= 512 for c in chunks)


def test_empty_text_returns_empty():
    assert chunk_text("") == []


def test_invalid_size_raises():
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=10, overlap=10)
