"""端到端冒烟脚本：临时目录全链路验证，零外部依赖（stub 嵌入 + 本地 Qdrant + SQLite）。

用法：uv run python scripts/smoke.py
退出码 0 = 通过；作为后续所有阶段的回归基线（docs/quality.md P0）。
"""
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.config import Settings


def _check(condition: bool, message: str) -> None:
    """冒烟断言：失败立即终止并给出上下文（不用 assert，-O 优化模式下同样生效）。"""
    if not condition:
        raise SystemExit(f"冒烟失败：{message}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rag-smoke-"))
    settings = Settings(
        data_dir=tmp,
        embedding_backend="stub",
        embedding_dim=64,
        # 冒烟不依赖真实 LLM：阈值 1.0 使命中走拒答路径（不调用 LLM）
        refusal_threshold=1.0,
        llm_base_url="http://127.0.0.1:9/v1",  # 不可达端点；拒答路径不应触发调用
        llm_model="smoke",
        api_key="smoke-key",
    )
    app = create_app(settings)
    sample = tmp / "sample.md"
    sample.write_text("# 冒烟\n\n量子计算使用量子比特与叠加态。", encoding="utf-8")

    with TestClient(app, headers={"Authorization": "Bearer smoke-key"}) as client:
        _check(client.get("/health").json()["success"], "health 失败")

        kb_id = client.post("/api/v1/kb", json={"name": "冒烟库"}).json()["data"]["id"]
        up = client.post(
            f"/api/v1/kb/{kb_id}/documents",
            files={"file": (sample.name, sample.read_bytes(), "text/markdown")},
        )
        _check(up.status_code == 201, f"上传失败：{up.text}")
        _check(up.json()["data"]["status"] == "ready", "摄取未就绪")

        hits = client.post(
            "/api/v1/search", json={"query": "量子比特", "kb_id": kb_id, "top_k": 3}
        ).json()["data"]
        _check(bool(hits) and bool(hits[0]["content"]), "检索无结果")

        chat = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "什么是量子比特？"}],
                "rag_kb_id": kb_id,
            },
        )
        _check(chat.status_code == 200, f"chat 失败：{chat.text}")
        _check(
            chat.json()["choices"][0]["message"]["content"] == "知识库中未找到相关内容。",
            "拒答路径不符合预期",
        )

        doc_id = up.json()["data"]["id"]
        _check(
            client.delete(f"/api/v1/kb/{kb_id}/documents/{doc_id}").status_code == 200,
            "文档删除失败",
        )
        _check(
            client.post("/api/v1/search", json={"query": "量子比特", "kb_id": kb_id}).json()[
                "data"
            ]
            == [],
            "删除后仍可检索",
        )

    print("冒烟通过：上传 → 检索 → RAG 拒答 → 删除 全链路 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
