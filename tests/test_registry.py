"""注册表单测：知识库、文档生命周期、chunk 幂等替换、级联删除。"""
from core.ingest.chunker import Chunk
from core.storage.registry import Registry


def _make_reg(tmp_path) -> Registry:
    return Registry(f"sqlite:///{tmp_path / 'registry.db'}")


def test_create_and_list_kbs(tmp_path):
    reg = _make_reg(tmp_path)
    kb = reg.create_kb("测试库", "document")
    assert kb.id
    assert kb.kb_type == "document"
    assert [k.name for k in reg.list_kbs()] == ["测试库"]
    assert reg.get_kb(kb.id).name == "测试库"
    assert reg.get_kb("nonexistent") is None


def test_document_lifecycle(tmp_path):
    reg = _make_reg(tmp_path)
    kb = reg.create_kb("kb")
    doc = reg.create_document(kb.id, "note.md", "upload://note.md", "hash-1")
    assert doc.status == "uploaded"  # 状态机契约：文档初始为 uploaded（异步管线）
    assert reg.get_document(kb.id, doc.id).title == "note.md"
    # 跨库访问隔离
    other = reg.create_kb("other")
    assert reg.get_document(other.id, doc.id) is None


def test_set_chunks_is_idempotent_replace(tmp_path):
    reg = _make_reg(tmp_path)
    kb = reg.create_kb("kb")
    doc = reg.create_document(kb.id, "d.md", "upload://d.md", "hash-1")

    first = reg.set_chunks(doc.id, kb.id, [Chunk(text="旧内容", index=0, start=0, end=3)])
    second = reg.set_chunks(
        doc.id, kb.id, [Chunk(text="新内容一", index=0, start=0, end=4),
                        Chunk(text="新内容二", index=1, start=4, end=8)]
    )
    assert first != second
    assert len(reg.get_chunk_contents(second)) == 2
    assert reg.get_chunk_contents(first) == {}
    fresh = reg.get_document(kb.id, doc.id)
    assert fresh.chunk_count == 2
    assert fresh.status == "ready"


def test_mark_document_failed(tmp_path):
    reg = _make_reg(tmp_path)
    kb = reg.create_kb("kb")
    doc = reg.create_document(kb.id, "bad.pdf", "upload://bad.pdf", "hash-1")
    reg.mark_document_failed(doc.id, "解析失败：损坏的 PDF")
    fresh = reg.get_document(kb.id, doc.id)
    assert fresh.status == "failed"
    assert "损坏" in fresh.error


def test_delete_document_cascades_chunks(tmp_path):
    reg = _make_reg(tmp_path)
    kb = reg.create_kb("kb")
    doc = reg.create_document(kb.id, "d.md", "upload://d.md", "hash-1")
    ids = reg.set_chunks(doc.id, kb.id, [Chunk(text="内容", index=0, start=0, end=2)])

    reg.delete_document(kb.id, doc.id)
    assert reg.count_documents(kb.id) == 0
    assert reg.get_document(kb.id, doc.id) is None
    assert reg.get_chunk_contents(ids) == {}


def test_document_failed_then_reingested_recovers(tmp_path):
    reg = _make_reg(tmp_path)
    kb = reg.create_kb("kb")
    doc = reg.create_document(kb.id, "d.md", "upload://d.md", "hash-1")
    reg.mark_document_failed(doc.id, "临时错误")
    reg.set_chunks(doc.id, kb.id, [Chunk(text="修复后内容", index=0, start=0, end=5)])
    fresh = reg.get_document(kb.id, doc.id)
    assert fresh.status == "ready"
    assert fresh.error is None


# ---------- 分页 ----------


def test_kbs_enriched_pagination(tmp_path):
    reg = _make_reg(tmp_path)
    for i in range(5):
        reg.create_kb(f"库-{i}", "document")

    # 全量（兼容旧调用方）
    assert len(reg.list_kbs_enriched()) == 5
    # 每页 2 条
    page1 = reg.list_kbs_enriched(page=1, page_size=2)
    page2 = reg.list_kbs_enriched(page=2, page_size=2)
    page3 = reg.list_kbs_enriched(page=3, page_size=2)
    ids1 = {k["id"] for k in page1}
    ids2 = {k["id"] for k in page2}
    ids3 = {k["id"] for k in page3}
    assert len(ids1) == 2 and len(ids2) == 2 and len(ids3) == 1
    # 不重叠且全覆盖
    assert ids1.isdisjoint(ids2) and ids1.isdisjoint(ids3) and ids2.isdisjoint(ids3)
    assert ids1 | ids2 | ids3 == {k["id"] for k in reg.list_kbs_enriched()}
    # count 与分页和一致
    assert reg.count_kbs() == 5
    # 越界页返回空
    assert reg.list_kbs_enriched(page=9, page_size=2) == []


def test_documents_pagination(tmp_path):
    reg = _make_reg(tmp_path)
    kb = reg.create_kb("kb")
    for i in range(7):
        reg.create_document(kb.id, f"doc-{i}.md", f"upload://doc-{i}.md", f"hash-{i}")

    # 全量
    assert len(reg.list_documents(kb.id)) == 7
    assert reg.count_documents(kb.id) == 7
    # 每页 3 条
    p1 = [d.id for d in reg.list_documents(kb.id, page=1, page_size=3)]
    p2 = [d.id for d in reg.list_documents(kb.id, page=2, page_size=3)]
    p3 = [d.id for d in reg.list_documents(kb.id, page=3, page_size=3)]
    assert len(p1) == 3 and len(p2) == 3 and len(p3) == 1
    assert len(set(p1 + p2 + p3)) == 7
    # 越界页空
    assert reg.list_documents(kb.id, page=9, page_size=3) == []
    # 空 KB 文档数 0
    empty = reg.create_kb("empty")
    assert reg.list_documents(empty.id, page=1, page_size=3) == []
    assert reg.count_documents(empty.id) == 0
