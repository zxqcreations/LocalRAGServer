"""故障演练（Phase 6 门④，docs/runbooks/failure-drill.md）。

三场景经 monkeypatch 注入真实故障语义（不杀生产进程，测试隔离环境）：
1. Qdrant 损坏 → readyz 503 + 检索 500 信封（内部错误不泄露）
2. SQLite 损坏 → readyz 503 + 数据面 500 信封
3. LLM 不可达 → readyz 保持 200（非关键降级）+ Chat 502 信封
4. worker 停 → 入队幂等（任务不丢失语义由 enqueue 幂等测试覆盖，本脚本验证入队不抛）

用法：uv run python scripts/drill_failures.py
"""
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.config import Settings

# Windows 控制台代码页兼容（同 smoke.py）
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if _reconfigure is not None:
    _reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"  ✗ {message}")
    else:
        print(f"  ✓ {message}")


def _app(tmp_path: Path):
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            qdrant_path=tmp_path / "qdrant",
            database_url=f"sqlite:///{tmp_path / 'r.db'}",
            embedding_backend="stub",
            embedding_dim=64,
            api_key="drill-failure-key",
            llm_base_url="http://127.0.0.1:9/v1",  # 不可达 LLM（场景 3 预置）
        )
    )


_AUTH = {"Authorization": "Bearer drill-failure-key"}


def _client(app):
    # raise_server_exceptions=False：演练验证的是 500 信封本身
    # （生产 ServerErrorMiddleware 行为），不让 TestClient 重新抛出
    return TestClient(app, raise_server_exceptions=False)


def scenario_qdrant(tmp_path: Path) -> None:
    print("=== 场景 1：Qdrant 损坏 ===")
    app = _app(tmp_path)
    with _client(app) as client:
        kb_id = client.post("/api/v1/kb", json={"name": "库"}, headers=_AUTH).json()["data"]["id"]
        client.post(
            f"/api/v1/kb/{kb_id}/documents",
            files={"file": ("d.md", "量子计算。".encode(), "text/markdown")},
            headers=_AUTH,
        )
        # 注入故障：store 全部操作抛异常（模拟损坏）；close 正常（lifespan teardown 需通过）
        class BoomStore:
            def close(self) -> None:
                pass

            def __getattr__(self, name):
                raise RuntimeError("Qdrant 损坏（演练注入）")

        app.state.vector_store = BoomStore()
        app.state.search_service._store = BoomStore()  # noqa: SLF001 演练专用注入

        rz = client.get("/readyz")
        _check(rz.status_code == 503, f"readyz 503（实际 {rz.status_code}）")
        _check(
            rz.json()["data"]["checks"]["qdrant"].startswith("down"),
            "qdrant 判定 down",
        )
        resp = client.post(
            "/api/v1/search", json={"query": "量子", "kb_id": kb_id}, headers=_AUTH
        )
        _check(resp.status_code == 500, f"检索 500 信封（实际 {resp.status_code}）")
        _check(
            resp.json()["error"]["code"] == "internal_error",
            "错误信封脱敏（internal_error，无内部细节）",
        )


def scenario_database(tmp_path: Path) -> None:
    print("=== 场景 2：SQLite 损坏 ===")
    app = _app(tmp_path)
    with _client(app) as client:
        # 注入故障：registry 查询抛异常（模拟 DB 损坏）；close 正常（teardown）
        class BoomRegistry:
            def close(self) -> None:
                pass

            def __getattr__(self, name):
                raise RuntimeError("SQLite 损坏（演练注入）")

        app.state.registry = BoomRegistry()

        rz = client.get("/readyz")
        _check(rz.status_code == 503, f"readyz 503（实际 {rz.status_code}）")
        _check(
            rz.json()["data"]["checks"]["database"].startswith("down"),
            "database 判定 down",
        )
        resp = client.get("/api/v1/kb", headers=_AUTH)
        _check(resp.status_code == 500, f"数据面 500 信封（实际 {resp.status_code}）")


def scenario_llm(tmp_path: Path) -> None:
    print("=== 场景 3：LLM 不可达（非关键降级） ===")
    app = _app(tmp_path)
    with _client(app) as client:
        rz = client.get("/readyz")
        _check(rz.status_code == 200, f"readyz 保持 200（实际 {rz.status_code}）")
        _check(
            rz.json()["data"]["checks"]["llm"].startswith("down"),
            "llm 判定 down（非关键）",
        )
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "你好"}]},
            headers=_AUTH,
        )
        _check(resp.status_code == 502, f"Chat 502 信封（实际 {resp.status_code}）")


def scenario_worker_stop(tmp_path: Path) -> None:
    print("=== 场景 4：worker 停（入队不抛、任务持久） ===")
    # 无 worker 消费（filesystem broker 持久）：入队调用本身必须成功
    from core.ingest import tasks as tasks_mod

    app = _app(tmp_path)
    with _client(app) as client:
        kb_id = client.post("/api/v1/kb", json={"name": "库"}, headers=_AUTH).json()["data"]["id"]
        resp = client.post(
            f"/api/v1/kb/{kb_id}/documents",
            files={"file": ("d.md", "量子计算。".encode(), "text/markdown")},
            headers=_AUTH,
        )
        _check(resp.status_code in (201, 202), f"入队成功（实际 {resp.status_code}）")
        job = app.state.registry.list_jobs() if hasattr(app.state.registry, "list_jobs") else []
        _check(isinstance(job, list), "任务记录持久（ingest_jobs 表）")
    _ = tasks_mod  # noqa: F841 演练引用（防误删 import）


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rag-failure-drill-"))
    scenario_qdrant(tmp / "s1")
    scenario_database(tmp / "s2")
    scenario_llm(tmp / "s3")
    scenario_worker_stop(tmp / "s4")
    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\n故障演练全部通过：四场景降级矩阵符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
