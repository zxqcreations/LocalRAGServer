"""FastAPI 应用工厂、生命周期、认证中间件与统一异常信封。"""
import json
import logging
from contextlib import asynccontextmanager
from secrets import compare_digest

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from apps.api.errors import (
    ACL_DENIED,
    AUTH_INVALID,
    AUTH_REQUIRED,
    AUTH_UNCONFIGURED,
    RATE_LIMITED,
)
from apps.api.routes import chat, documents, kb, search
from apps.api.schemas import err
from core.config import Settings, get_settings
from core.generation.llm import ChatClient
from core.retrieval.embeddings import build_embedder
from core.retrieval.rerank import build_reranker
from core.retrieval.search import SearchService
from core.security.acl import AclDeniedError, resolve_allowed_kb_ids
from core.security.ratelimit import build_limiter
from core.storage.registry import Registry
from core.storage.vector import QdrantVectorStore

logger = logging.getLogger("local_rag_server")

# 无需认证的路径（健康检查/探针与 API 文档）
_PUBLIC_PATHS = {"/health", "/healthz", "/readyz", "/docs", "/openapi.json", "/redoc"}

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
        app.state.limiter = build_limiter()
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

    @app.get("/healthz", tags=["运维"])
    def healthz():
        """存活探针：进程在即 ok（审计 ARC-010）。"""
        return {"success": True, "data": {"status": "ok"}, "error": None, "meta": None}

    @app.get("/readyz", tags=["运维"])
    def readyz():
        """就绪探针：各依赖连通性逐项（审计 ARC-010/F-16 口径）。

        database/qdrant 为关键依赖（失败 → 503 degraded）；
        embedder/llm 失败降级记录但不阻断就绪判定。
        """
        checks: dict[str, str] = {}

        def _check(name: str, fn) -> None:
            try:
                fn()
                checks[name] = "ok"
            except Exception as exc:
                checks[name] = f"down: {type(exc).__name__}"

        def _db() -> None:
            app.state.registry.list_kbs()

        def _qdrant() -> None:
            app.state.vector_store.ensure_collection(settings.embedding_dim)

        def _embedder() -> None:
            if app.state.search_service.embedder.dim <= 0:
                raise RuntimeError("embedder dim 无效")

        def _llm() -> None:
            app.state.chat_client.client.get("/models").raise_for_status()

        _check("database", _db)
        _check("qdrant", _qdrant)
        _check("embedder", _embedder)
        _check("llm", _llm)
        critical_down = [k for k in ("database", "qdrant") if checks[k] != "ok"]
        status = 503 if critical_down else 200
        return JSONResponse(
            status_code=status,
            content={
                "success": not critical_down,
                "data": {
                    "status": "ready" if not critical_down else "degraded",
                    "checks": checks,
                },
                "error": None,
                "meta": None,
            },
        )

    # ---- 认证与 ACL 强制点（审计 F-01/F-13：单一入口，服务端推导允许的 KB 集合） ----

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
        raw_key = header[len("Bearer ") :]
        # 认证前 per-IP 限流（审计 M-2：防 scrypt 计算放大 DoS 与失败尝试爆破）
        ip = request.client.host if request.client else "unknown"
        if not app.state.limiter.allow(f"ip:{ip}", 30, 0.5):
            return JSONResponse(
                status_code=429, content=err(RATE_LIMITED, "请求过于频繁，请稍后重试")
            )
        # 1) 引导主 Key（settings.api_key）：全权限，Phase 4 Web 端接管签发
        if compare_digest(raw_key, settings.api_key):
            request.state.allowed_kbs = "*"
            request.state.actor = "master"
            if not app.state.limiter.allow("key:master", 120, 2.0):
                return JSONResponse(
                    status_code=429, content=err(RATE_LIMITED, "请求过于频繁，请稍后重试")
                )
            return await call_next(request)
        # 2) api_keys 表 Key：scrypt 验证 + ACL 解析（F-13 强制点）
        record = app.state.registry.verify_api_key(raw_key)
        if record is None:
            return JSONResponse(status_code=401, content=err(AUTH_INVALID, "API Key 无效"))
        request.state.allowed_kbs = resolve_allowed_kb_ids(json.loads(record.kb_acl))
        request.state.api_key_record = record
        request.state.actor = record.id
        if not app.state.limiter.allow(f"key:{record.id}", 120, 2.0):
            return JSONResponse(
                status_code=429, content=err(RATE_LIMITED, "请求过于频繁，请稍后重试")
            )
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

    @app.exception_handler(AclDeniedError)
    async def _acl_exc(request: Request, exc: AclDeniedError):
        # 审计 F-13：越权显式 403（不用 404/空结果掩盖，避免可探测性歧义）
        return JSONResponse(status_code=403, content=err(ACL_DENIED, str(exc)))

    @app.exception_handler(Exception)
    async def _unhandled_exc(request: Request, exc: Exception):
        # 不向客户端泄露内部细节，完整堆栈记录在服务端日志
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500, content=err("internal_error", "服务内部错误，详情见服务端日志")
        )

    return app
