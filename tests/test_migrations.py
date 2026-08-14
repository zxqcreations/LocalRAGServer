"""迁移体系测试：baseline 可在空库应用且可逆回滚（审计 ARC-008 可逆性要求）。"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "RAG_DATABASE_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_baseline_upgrade_creates_tables(tmp_path):
    db = tmp_path / "mig.db"
    result = _alembic(f"sqlite:///{db}", "upgrade", "head")
    assert result.returncode == 0, result.stderr[-800:]
    from sqlalchemy import create_engine, inspect

    names = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert {"knowledgebase", "document", "chunkrow"} <= names


def test_baseline_downgrade_is_reversible(tmp_path):
    db = tmp_path / "mig.db"
    up = _alembic(f"sqlite:///{db}", "upgrade", "head")
    assert up.returncode == 0, up.stderr[-800:]
    down = _alembic(f"sqlite:///{db}", "downgrade", "base")
    assert down.returncode == 0, down.stderr[-800:]
    from sqlalchemy import create_engine, inspect

    # 应用表全部移除；alembic_version 为 alembic 自身版本簿记表，回滚后保留
    names = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert names <= {"alembic_version"}
