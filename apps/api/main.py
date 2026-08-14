"""FastAPI 应用工厂、生命周期、认证中间件与统一异常信封。"""
import logging
from contextlib import asynccontextmanager
from secrets import compare_digest

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from apps.api.errors import AUTH_INVALID, AUTH_REQUIRED, AUTH_UNCONFIGURED
from apps.api.routes import chat, documents, kb, search
from apps.api.schemas import err
from core.config import Settings, get_settings
from core.generation.llm import ChatClient
from core.retrieval.embeddings import build_embedder
from core.retrieval.rerank import build_reranker
from core.retrieval.search import SearchService
from core.storage.registry import Registry
from core.storage.vector import QdrantVectorStore

logger = logging.getLogger("local_rag_server")

# 无需认证的路径（健康检查与 API 文档）
_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if settings.database_url is None:  # validator 已派生，此处为 fail-fast 兜底
            raise RuntimeError("database_url 未配置（应由 data_dir 派生）")
        if settings.host not in _LOOPBACK_HOSTS:
            # 审计 F-01：对外暴露需显式确认，启动时打印安全警告
            logger.warning(
                "安全警告：服务绑定 %s（非回环地址）。确认已配置 TLS 与 API Key（当前：%s）",
                settings.host,
                "已配置" if settings.api_key else "未配置（fail-closed）",
            )
        app.state.registry = Registry(settings.database_url)
        embedder = build_embedder(settings)
        app.state.vector_store = QdrantVectorStore(
            url=settings.qdrant_url,
            path=settings.qdrant_path,
            hnsw_m=settings.hnsw_m,
            hnsw_ef_construct=settings.hnsw_ef_construct,
            hnsw_ef=settings.hnsw_ef,
        )
        app.state.search_service = SearchService(
            app.state.vector_store,
            app.state.registry,
            embedder,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            max_pdf_pages=settings.max_pdf_pages,
            reranker=build_reranker(settings),
            retrieval_top_k=settings.retrieval_top_k,
            rerank_top_k=settings.rerank_top_k,
        )
        app.state.search_service.ensure_ready()
        app.state.chat_client = ChatClient(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
            settings.llm_timeout,
        )
        try:
            yield
        finally:
            app.state.chat_client.close()
            app.state.vector_store.close()
            app.state.registry.close()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    app.include_router(kb.router, prefix="/api/v1", tags=["知识库"])
    app.include_router(documents.router, prefix="/api/v1", tags=["文档"])
    app.include_router(search.router, prefix="/api/v1", tags=["检索"])
    app.include_router(chat.router, prefix="/v1", tags=["Chat"])

    @app.get("/health", tags=["运维"])
    def health():
        return {"success": True, "data": {"status": "ok"}, "error": None, "meta": None}

    # ---- 最小认证骨架（审计 F-01：随第一批接口同批交付；KB 级 ACL 属 Phase 3） ----

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        if not settings.api_key:
            return JSONResponse(
                status_code=503,
                content=err(
                    AUTH_UNCONFIGURED, "服务未配置 API Key（fail-closed，见 .env.example）"
                ),
            )
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse(
                status_code=401, content=err(AUTH_REQUIRED, "缺少 Bearer API Key")
            )
        if not compare_digest(header[len("Bearer ") :], settings.api_key):
            return JSONResponse(status_code=401, content=err(AUTH_INVALID, "API Key 无效"))
        return await call_next(request)

    # ---- 统一异常信封 ----

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        # raise_http 的 detail 为 {code, message}；其余为兜底
        if isinstance(exc.detail, dict):
            code = exc.detail.get("code", "http_error")
            message = exc.detail.get("message", "")
            return JSONResponse(status_code=exc.status_code, content=err(code, message))
        return JSONResponse(
            status_code=exc.status_code, content=err("http_error", str(exc.detail))
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422, content=err("validation_error", "请求参数校验失败")
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc(request: Request, exc: Exception):
        # 不向客户端泄露内部细节，完整堆栈记录在服务端日志
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500, content=err("internal_error", "服务内部错误，详情见服务端日志")
        )

    return app
