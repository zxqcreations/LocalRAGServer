"""全局配置：环境变量 RAG_* 优先，其次 .env 文件。

设计约束（docs/quality.md P0-3，审计 AUD-03/04/08/09/12、F-06）：
- 数据路径从 data_dir 单一来源派生（RAG_QDRANT_PATH / RAG_DATABASE_URL 可显式覆盖）
- 未知 RAG_* 环境变量 fail-fast（extra=forbid，防拼写错误静默漂移）
- 非 stub 嵌入后端启动时校验必需配置
- 默认端点 vLLM 口径（9001），不指向不存在的 Ollama（11434）
"""
import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAG_", extra="forbid")

    # 应用
    app_name: str = "LocalRAGServer"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("data")

    # 向量库：qdrant_url 为空 => 本地嵌入模式（免 Docker）
    qdrant_url: str | None = None
    qdrant_path: Path | None = None  # None => 派生自 data_dir
    hnsw_m: int = 16
    hnsw_ef_construct: int = 200
    hnsw_ef: int = 256

    # 嵌入后端：stub | local | tei | openai
    embedding_backend: str = "stub"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    tei_url: str = "http://127.0.0.1:9002"
    openai_base_url: str = "http://127.0.0.1:9002/v1"  # TEI 的 OpenAI 兼容端点
    openai_api_key: str = ""

    # LLM（OpenAI 兼容端点：架构默认 vLLM 9001；Spike 后可能切 llama.cpp/llama-server）
    llm_base_url: str = "http://127.0.0.1:9001/v1"
    llm_api_key: str = ""
    llm_model: str = "Qwen3-8B-AWQ"
    llm_timeout: float = 120.0

    # URL 摄取与 SSRF 防护（审计 F-10：防护与能力同批交付）
    url_allowlist: str = ""  # 逗号分隔域名白名单；空 = 任意公网（解析后 IP 校验兜底）
    url_fetch_max_bytes: int = 5 * 1024 * 1024
    url_fetch_max_redirects: int = 3
    url_fetch_timeout: float = 15.0
    url_fetch_allow_loopback: bool = False  # 仅测试/开发：允许本机地址（生产必须 False）

    # 任务队列（ADR-002：本机 filesystem 代理，生产切 Redis）
    celery_broker_url: str = "filesystem://"

    # 限流（ADR-005 Phase 6）：redis_url 非空 => Redis 令牌桶（跨进程一致），
    # 空 => 进程内内存实现；Redis 不可用时 fail-open 回退内存（见 build_limiter）
    redis_url: str | None = None

    # 认证（审计 F-01：最小认证骨架随第一批接口同批交付；KB 级 ACL 属 Phase 3）
    # 空值 => fail-closed：业务接口全部拒绝，仅 /health 开放
    api_key: str = ""

    # 上传防护（审计 F-07/F-09）
    max_upload_mb: int = 200
    max_pdf_pages: int = 3000

    # 生成策略：检索最高分低于阈值 => 拒答（Phase 2 起按 KB 校准，见 quality.md）
    refusal_threshold: float = 0.25

    # 检索参数（Phase 0 用 chunk/HNSW；Phase 2 起用全量，见架构 §6/§8.2）
    chunk_size: int = 512
    chunk_overlap: int = 64
    parent_chunk_size: int = 2048
    retrieval_top_k: int = 50
    search_top_k: int = 5
    rerank_top_k: int = 8
    rrf_k: int = 60
    embed_batch_size: int = 128
    rerank_backend: str = "off"  # off | tei（Phase 2）
    rerank_url: str = "http://127.0.0.1:9003"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # 元数据（MVP 用 SQLite；Phase 1+ 换 PostgreSQL + Alembic）
    database_url: str | None = None  # None => 派生自 data_dir

    @model_validator(mode="after")
    def _derive_paths(self) -> "Settings":
        if self.qdrant_path is None:
            self.qdrant_path = self.data_dir / "qdrant"
        if self.database_url is None:
            self.database_url = f"sqlite:///{self.data_dir / 'registry.db'}"
        return self

    @model_validator(mode="after")
    def _validate_backends(self) -> "Settings":
        if self.embedding_backend not in {"stub", "local", "tei", "openai"}:
            raise ValueError(
                f"未知嵌入后端：{self.embedding_backend}（可选 stub|local|tei|openai）"
            )
        if self.embedding_backend == "openai" and not self.openai_api_key:
            raise ValueError("embedding_backend=openai 必须配置 RAG_OPENAI_API_KEY")
        return self

    @model_validator(mode="after")
    def _reject_unknown_env(self) -> "Settings":
        """fail-fast 防漂移：pydantic-settings 只读已知字段，未知 RAG_* 需显式扫描拒绝。"""
        valid = {f"RAG_{name.upper()}" for name in type(self).model_fields}
        unknown = sorted(k for k in os.environ if k.startswith("RAG_") and k not in valid)
        if unknown:
            raise ValueError(
                "检测到未知 RAG_* 环境变量（拼写错误将导致静默使用默认值）："
                + ", ".join(unknown)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
