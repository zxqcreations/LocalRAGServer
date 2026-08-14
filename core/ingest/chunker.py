"""文档分块：分隔符递归切分 + 按目标大小合并 + 尾部重叠。

MVP 为单层分块；parent-child 双层结构在 Phase 2 引入。
"""
from dataclasses import dataclass

DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", "；", ";", " "]


@dataclass(frozen=True)
class Chunk:
    """一个检索块：text 为内容，start/end 为原文中的字符偏移。"""

    text: str
    index: int
    start: int
    end: int


@dataclass(frozen=True)
class _Piece:
    """带原文偏移的原子片段（分隔符已在切分时丢弃）。"""

    text: str
    start: int
    end: int


def _split(text: str, separators: list[str], base: int = 0) -> list[_Piece]:
    """按第一个出现的分隔符递归切分，返回带原文偏移的原子片段。"""
    if not text:
        return []
    sep = next((s for s in separators if s in text), None)
    if sep is None:
        return [_Piece(text, base, base + len(text))]
    pieces: list[_Piece] = []
    offset = base
    for part in text.split(sep):
        pieces.extend(_split(part, separators, offset))
        offset += len(part) + len(sep)
    return pieces


def chunk_text(
    text: str,
    chunk_size: int = 512,
    overlap: int = 64,
    separators: list[str] | None = None,
) -> list[Chunk]:
    """把文本切分为大小受控、相邻重叠的块。

    - 原子片段按 chunk_size 贪心合并；
    - 每次结算后保留尾部 overlap 字符，作为下一块的头部（保证相邻块重叠）；
    - 超过 chunk_size 的不可切分片段按步长 chunk_size - overlap 硬切。
    """
    if chunk_size <= overlap:
        raise ValueError("chunk_size 必须大于 overlap")
    seps = DEFAULT_SEPARATORS if separators is None else separators
    pieces = _split(text, seps)

    merged: list[tuple[str, int, int]] = []  # (text, start, end)
    buf: list[_Piece] = []
    size = 0

    def flush(keep: int) -> None:
        """结算缓冲为一个 chunk；keep > 0 时保留尾部 keep 字符作为重叠。"""
        nonlocal buf, size
        joined = "".join(p.text for p in buf)
        merged.append((joined, buf[0].start, buf[-1].end))
        tail = joined[-keep:] if keep > 0 else ""
        if tail:
            buf = [_Piece(tail, buf[-1].end - len(tail), buf[-1].end)]
            size = len(tail)
        else:
            buf = []
            size = 0

    for piece in pieces:
        if len(piece.text) > chunk_size:
            if buf:
                flush(0)
            step = max(1, chunk_size - overlap)
            for i in range(0, len(piece.text), step):
                seg = piece.text[i : i + chunk_size]
                merged.append((seg, piece.start + i, piece.start + i + len(seg)))
            continue
        if buf and size + len(piece.text) > chunk_size:
            flush(overlap)
        buf.append(piece)
        size += len(piece.text)

    if buf:
        joined = "".join(p.text for p in buf)
        merged.append((joined, buf[0].start, buf[-1].end))

    return [Chunk(text=t, index=i, start=s, end=e) for i, (t, s, e) in enumerate(merged)]
