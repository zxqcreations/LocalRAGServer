"""批量导入测试（幂等键 (kb_id, content_hash)、source 落盘、入队统计）。"""
from core.ingest.importer import import_directory
from core.storage.registry import Registry


def _make_tree(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("内容甲。", encoding="utf-8")
    (root / "b.txt").write_text("内容乙。", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "c.md").write_text("内容丙。", encoding="utf-8")
    (root / "same-as-a.md").write_text("内容甲。", encoding="utf-8")  # 与 a.md 同内容
    (root / "ignore.docx").write_text("x", encoding="utf-8")  # 不支持格式
    return root


def test_import_directory_basic(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    kb = registry.create_kb("库")
    enqueued: list[str] = []

    stats = import_directory(
        registry, kb.id, _make_tree(tmp_path), tmp_path / "work", enqueued.append
    )
    assert stats.total == 4  # 支持格式 4 个（docx 被排除）
    assert stats.new == 3  # a/b/c
    assert stats.skipped == 1  # same-as-a.md 同内容去重
    assert stats.failed == 0
    assert len(enqueued) == 3
    # source 落盘且保留扩展名
    assert (tmp_path / "work" / enqueued[0] / "source.md").exists()


def test_import_directory_idempotent_rerun(tmp_path):
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    kb = registry.create_kb("库")
    enqueued: list[str] = []
    root = _make_tree(tmp_path)

    first = import_directory(registry, kb.id, root, tmp_path / "work", enqueued.append)
    second = import_directory(registry, kb.id, root, tmp_path / "work", enqueued.append)
    assert first.new == 3
    assert second.new == 0
    assert second.skipped == 4  # 全部命中 (kb_id, hash) 幂等键
    assert len(enqueued) == 3


def test_same_file_into_another_kb_is_allowed(tmp_path):
    # 审计 ARC-018：同一文件可入多 KB（dedup 键含 kb_id）
    registry = Registry(f"sqlite:///{tmp_path / 'r.db'}")
    kb1 = registry.create_kb("库一")
    kb2 = registry.create_kb("库二")
    enqueued: list[str] = []
    root = _make_tree(tmp_path)

    s1 = import_directory(registry, kb1.id, root, tmp_path / "w1", enqueued.append)
    s2 = import_directory(registry, kb2.id, root, tmp_path / "w2", enqueued.append)
    assert s1.new == 3
    assert s2.new == 3  # 跨 KB 不去重
    assert len(enqueued) == 6
