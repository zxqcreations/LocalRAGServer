"""MCP Server 测试（审计 F7：内存 ClientSession 无网络测试工具注册与调用）。"""
import pytest

from apps.mcp.server import build_mcp_server
from core.config import Settings, get_settings
from core.retrieval.embeddings import StubEmbedder
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import InMemoryVectorStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    # 环境注入：celery 任务的 _pipeline() 读全局 get_settings()，必须与夹具同源
    settings = Settings(data_dir=tmp_path, embedding_backend="stub", embedding_dim=64)
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("RAG_EMBEDDING_BACKEND", "stub")
    monkeypatch.setenv("RAG_EMBEDDING_DIM", "64")
    get_settings.cache_clear()
    registry = Registry(settings.database_url)
    search_service = SearchService(
        store=InMemoryVectorStore(), registry=registry, embedder=StubEmbedder(dim=64)
    )
    search_service.ensure_ready()
    settings = Settings(data_dir=tmp_path, embedding_backend="stub", embedding_dim=64)
    kb = registry.create_kb("技术库")
    src = tmp_path / "d.md"
    src.write_text("# 文档\n\n量子计算使用量子比特与叠加态。", encoding="utf-8")
    search_service.ingest_file(kb.id, src)
    return registry, search_service, settings, kb


async def _session(env, allowed_resolver=None):
    import asyncio
    import contextlib

    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    registry, search_service, settings, _ = env
    server = build_mcp_server(registry, search_service, settings, allowed_resolver)

    @contextlib.asynccontextmanager
    async def cm():
        async with create_client_server_memory_streams() as (cs, ss):
            task = asyncio.create_task(
                server.run(*ss, server.create_initialization_options())
            )
            try:
                async with ClientSession(*cs) as client:
                    await client.initialize()
                    yield server, client
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return cm()


@pytest.mark.anyio
async def test_tool_registration(env):
    async with (await _session(env)) as (_, client):
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {
            "search_knowledge",
            "list_knowledge_bases",
            "ask",
            "ingest_document",
            "get_document_status",
        }


@pytest.mark.anyio
async def test_search_knowledge_returns_chunks(env):
    async with (await _session(env)) as (_, client):
        result = await client.call_tool("search_knowledge", {"query": "量子比特", "kb": "技术库"})
        text = result.content[0].text
        assert "量子计算" in text
        assert "文档 d" in text or "d.md" in text


@pytest.mark.anyio
async def test_list_knowledge_bases(env):
    async with (await _session(env)) as (_, client):
        result = await client.call_tool("list_knowledge_bases", {})
        assert "技术库" in result.content[0].text


@pytest.mark.anyio
async def test_ingest_document_and_status(env, tmp_path):
    async with (await _session(env)) as (_, client):
        src = tmp_path / "new.md"
        src.write_text("新文档：经典力学三定律。", encoding="utf-8")
        result = await client.call_tool(
            "ingest_document", {"path": str(src), "kb": "技术库"}
        )
        text = result.content[0].text
        assert "job_id=" in text
        job_id = text.split("job_id=")[1]
        status = await client.call_tool("get_document_status", {"job_id": job_id})
        assert "job=" in status.content[0].text


@pytest.mark.anyio
async def test_acl_enforced_in_mcp(env):
    registry, search_service, settings, kb = env
    other = registry.create_kb("受限库")
    def allowed_resolver():
        return {kb.id}  # 仅技术库

    async with (
        await _session(env, allowed_resolver)
    ) as (_, client):
        # 越权库 → isError 结果（工具错误以内容返回，便于 Agent 消费）
        denied = await client.call_tool("search_knowledge", {"query": "x", "kb": other.id})
        assert denied.is_error
        assert "无权限" in denied.content[0].text
        # 授权库 → 正常
        ok = await client.call_tool("search_knowledge", {"query": "量子", "kb": "技术库"})
        assert not ok.is_error
        assert ok.content


@pytest.mark.anyio
async def test_unknown_kb_returns_error_result(env):
    async with (await _session(env)) as (_, client):
        result = await client.call_tool("search_knowledge", {"query": "x", "kb": "不存在"})
        assert result.is_error
        assert "知识库不存在" in result.content[0].text


@pytest.mark.anyio
async def test_ingest_rejected_when_local_paths_disabled(env, tmp_path):
    # 审计 H-2：远程通道（allow_local_paths=False）拒绝本地文件摄取
    src = tmp_path / "x.md"
    src.write_text("内容。", encoding="utf-8")

    registry, search_service, settings, _ = env
    server = build_mcp_server(
        registry, search_service, settings, allow_local_paths=False
    )
    import asyncio
    import contextlib

    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    @contextlib.asynccontextmanager
    async def cm():
        async with create_client_server_memory_streams() as (cs, ss):
            task = asyncio.create_task(
                server.run(*ss, server.create_initialization_options())
            )
            try:
                async with ClientSession(*cs) as client:
                    await client.initialize()
                    yield client
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async with cm() as client:
        result = await client.call_tool(
            "ingest_document", {"path": str(src), "kb": "技术库"}
        )
        assert result.is_error
        assert "不支持本地文件摄取" in result.content[0].text
