"""SQLite→PostgreSQL 迁移演练（quality.md Phase 1 退出标准：迁移 + 数据校验 + 回滚演练）。

流程：
1. 目标库不存在则创建
2. alembic upgrade head（PG 建表）
3. 双库写入同一批样例数据，逐项校验（KB/文档/chunk 计数与字段一致）
4. alembic downgrade base（回滚演练）→ 再 upgrade head（恢复）
5. 报告写入 docs/perf/pg-drill-<date>.md

用法：
  export RAG_DATABASE_URL='postgresql+psycopg://postgres:<密码>@127.0.0.1:5432/localrag'
  uv run python scripts/pg_drill.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from core.ingest.chunker import chunk_text
from core.storage.registry import Registry

PG_URL = os.environ.get("RAG_DATABASE_URL", "")
SQLITE_URL = "sqlite:///pg_drill_src.db"
OUT = Path(__file__).resolve().parents[1] / "docs" / "perf"


def run_alembic(*args: str) -> None:
    env = {**os.environ, "RAG_DATABASE_URL": PG_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(args)} 失败：{result.stderr[-500:]}")


def seed_and_compare(pg: Registry, sqlite: Registry) -> list[tuple[str, bool]]:
    """双库写入同一批数据并逐项校验，返回 (说明, 是否一致)。"""
    for reg, _label in ((sqlite, "SQLite"), (pg, "PG")):
        kb = reg.create_kb("drill-sample", "document")  # 同名同型，双库逐项可比
        doc = reg.create_document(kb.id, "sample.md", "drill://sample", "hash-drill")
        chunks = chunk_text("# 演练\n\n" + "迁移演练正文内容。" * 120)
        reg.set_chunks(doc.id, kb.id, chunks)

    def kb_by_name(reg: Registry, name: str):
        return next(k for k in reg.list_kbs() if k.name == name)

    sqlite_kb = kb_by_name(sqlite, "drill-sample")
    pg_kb = kb_by_name(pg, "drill-sample")
    sqlite_doc = sqlite.list_documents(sqlite_kb.id)[0]
    pg_doc = pg.list_documents(pg_kb.id)[0]
    return [
        ("KB 名称一致", sqlite_kb.name == pg_kb.name),
        ("文档数一致", sqlite.count_documents(sqlite_kb.id) == pg.count_documents(pg_kb.id)),
        ("chunk 数一致", sqlite_doc.chunk_count == pg_doc.chunk_count),
        ("pipeline_version 一致", sqlite_doc.pipeline_version == pg_doc.pipeline_version),
        ("状态一致", sqlite_doc.status == pg_doc.status),
    ]


def main() -> int:
    if not PG_URL:
        print("请先 export RAG_DATABASE_URL（postgresql+psycopg://...）")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    # 1. 确保目标库存在（连 postgres 维护库 CREATE DATABASE）
    import sqlalchemy

    target_db = PG_URL.rsplit("/", 1)[-1]
    admin_url = PG_URL.rsplit("/", 1)[0] + "/postgres"
    admin = sqlalchemy.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            sqlalchemy.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target_db}
        ).fetchone()
        if not exists:
            conn.execute(sqlalchemy.text(f'CREATE DATABASE "{target_db}"'))
            lines.append(f"创建目标库 {target_db}")
    admin.dispose()

    # 2. 迁移 + 回滚演练
    run_alembic("upgrade", "head")
    lines.append("alembic upgrade head（PG）通过")
    run_alembic("downgrade", "base")
    lines.append("alembic downgrade base（回滚演练）通过")
    run_alembic("upgrade", "head")
    lines.append("alembic upgrade head（恢复）通过")

    # 3. 数据校验
    sqlite_reg = Registry(SQLITE_URL)
    pg_reg = Registry(PG_URL)
    checks = seed_and_compare(pg_reg, sqlite_reg)
    lines.append("")
    lines.append("## 数据校验（双库同批写入逐项比对）")
    lines.extend(f"- {label}：{'OK' if ok else 'FAIL'}" for label, ok in checks)
    ok = all(v for _, v in checks)
    lines.append(f"\n结论：{'全部一致' if ok else '存在不一致'}")
    sqlite_reg.close()
    pg_reg.close()

    report = OUT / f"pg-drill-{time.strftime('%Y%m%d')}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
