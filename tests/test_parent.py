"""parent-child 回填测试（审计 F2/F6：命中回填、无孤儿、跨文档隔离）。"""
import pytest

from core.ingest.chunker import Chunk
from core.retrieval.parent import expand_parents
from core.storage.registry import Registry


@pytest.fixture
def env(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    kb = registry.create_kb("库")
    # 文档1：四个连续 chunk；文档2：两个独立 chunk
    doc1 = registry.create_document(kb.id, "d1.md", "t://d1", "h1")
    chunks1 = [
        Chunk(text=f"第一文档第{i}段内容" + "填充" * 30, index=i, start=0, end=10)
        for i in range(4)
    ]
    ids1 = registry.set_chunks(doc1.id, kb.id, chunks1)
    doc2 = registry.create_document(kb.id, "d2.md", "t://d2", "h2")
    chunks2 = [
        Chunk(text=f"第二文档第{i}段内容" + "填充" * 30, index=i, start=0, end=10)
        for i in range(2)
    ]
    ids2 = registry.set_chunks(doc2.id, kb.id, chunks2)
    return registry, kb, ids1, ids2


def test_expand_includes_neighboring_chunks(env):
    registry, kb, ids1, _ = env
    expanded = expand_parents(registry, kb.id, [ids1[1]], target_size=4096)
    assert ids1[1] in expanded
    text = expanded[ids1[1]]
    assert "第一文档第0段" in text  # 左邻
    assert "第一文档第2段" in text  # 右邻


def test_expand_does_not_cross_documents(env):
    registry, kb, ids1, ids2 = env
    expanded = expand_parents(registry, kb.id, [ids1[0]], target_size=4096)
    text = expanded[ids1[0]]
    assert "第二文档" not in text  # 不跨文档


def test_expand_small_target_returns_original(env):
    registry, kb, ids1, _ = env
    original = registry.get_chunk_contents([ids1[2]])[ids1[2]]
    expanded = expand_parents(registry, kb.id, [ids1[2]], target_size=1)
    assert expanded[ids1[2]] == original


def test_expand_empty_and_unknown_ids(env):
    registry, kb, _, _ = env
    assert expand_parents(registry, kb.id, []) == {}
    # 未知 id：无序列归属，退化为原文缺失 → 空串（调用方不传未知 id）
    result = expand_parents(registry, kb.id, ["nonexistent"], target_size=100)
    assert "nonexistent" in result


def test_all_hits_expanded_no_orphans(env):
    registry, kb, ids1, ids2 = env
    hits = ids1 + ids2  # 全部命中
    expanded = expand_parents(registry, kb.id, hits, target_size=2048)
    assert set(expanded.keys()) == set(hits)
    assert all(text for text in expanded.values())
