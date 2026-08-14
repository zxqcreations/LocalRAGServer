"""MCP stdio 入口（本机通道：主 Key 语义全权限，审计 F-11 本地特权通道）。

用法：uv run python scripts/mcp_stdio.py
Claude Code 配置示例（mcpServers）：
  {"local-rag": {"command": "uv", "args": ["run", "python", "scripts/mcp_stdio.py"]}}
"""
import asyncio

from apps.mcp.server import build_mcp_server
from core.config import get_settings
from core.retrieval.embeddings import build_embedder
from core.retrieval.rerank import build_reranker
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import QdrantVectorStore


async def main() -> None:
    settings = get_settings()
    if settings.database_url is None:
        raise RuntimeError("database_url 未配置")
    registry = Registry(settings.database_url)
    store = QdrantVectorStore(
        url=settings.qdrant_url,
        path=settings.qdrant_path,
        hnsw_m=settings.hnsw_m,
        hnsw_ef_construct=settings.hnsw_ef_construct,
        hnsw_ef=settings.hnsw_ef,
    )
    search_service = SearchService(
        store,
        registry,
        build_embedder(settings),
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
        max_pdf_pages=settings.max_pdf_pages,
        reranker=build_reranker(settings),
        retrieval_top_k=settings.retrieval_top_k,
        rerank_top_k=settings.rerank_top_k,
    )
    search_service.ensure_ready()
    server = build_mcp_server(registry, search_service, settings)
    async with server.run_stdio():
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
