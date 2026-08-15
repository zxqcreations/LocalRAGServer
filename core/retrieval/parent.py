"""parent-child 回填（架构 §5/§6：子块检索命中后扩展相邻上下文入生成）。

实现形态：不单独存父块（避免向量与存储膨胀）——命中子块后按文档内序号
向两侧扩展相邻 chunk 至目标尺寸，作为生成上下文。语义等价于父块回填。
"""
from core.storage.registry import Registry

DEFAULT_PARENT_SIZE = 2048  # 架构 §5 父块尺寸


def expand_parents(
    registry: Registry,
    kb_id: str,
    chunk_ids: list[str],
    target_size: int = DEFAULT_PARENT_SIZE,
    chunks_cache: tuple[list[str], dict[str, str], dict[str, str]] | None = None,
) -> dict[str, str]:
    """每个命中 chunk 扩展为 ~target_size 的上下文窗口（同文档相邻块向两侧拼接）。

    返回 chunk_id → 扩展后文本；无邻块时退化为原文。

    chunks_cache=(all_ids, ordered, doc_map)：KB 级 chunk 序列缓存（SearchService
    维护，摄取时失效）——万级文档下全量加载 list_chunks 是查询延迟大头
    （SLO 压测实测 ~430ms/查询），必须复用缓存；None 时自加载（兼容直接调用）。
    """
    if not chunk_ids:
        return {}
    if chunks_cache is not None:
        all_ids, ordered, doc_map = chunks_cache
    else:
        all_ids = [cid for cid, _ in registry.list_chunks(kb_id)]
        ordered = dict(registry.list_chunks(kb_id))
        doc_map = registry.get_chunk_doc_map(all_ids)
    seq_by_doc: dict[str, list[str]] = {}
    for cid in all_ids:
        doc_id = doc_map.get(cid)
        if doc_id is not None:
            seq_by_doc.setdefault(doc_id, []).append(cid)

    result: dict[str, str] = {}
    for cid in chunk_ids:
        doc_id = doc_map.get(cid)
        if doc_id is None:
            seq = [cid]
        else:
            seq = seq_by_doc.get(doc_id, [cid])
        idx = seq.index(cid) if cid in seq else 0
        parts = [ordered.get(cid, "")]
        size = len(parts[0])
        left, right = idx - 1, idx + 1
        while size < target_size:
            extended = False
            for side in ("right", "left"):  # 两侧轮流扩展
                pos = right if side == "right" else left
                if side == "right" and pos >= len(seq):
                    continue
                if side == "left" and pos < 0:
                    continue
                text = ordered.get(seq[pos], "")
                if side == "right":
                    parts.append(text)
                    right += 1
                else:
                    parts.insert(0, text)
                    left -= 1
                size += len(text)
                extended = True
                if size >= target_size:
                    break
            if not extended:
                break
        result[cid] = "\n\n".join(parts)
    return result
