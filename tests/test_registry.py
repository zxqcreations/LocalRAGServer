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
