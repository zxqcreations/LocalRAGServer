"""配置层单测（审计 F1/F-06/AUD-03/AUD-04/AUD-09）：路径派生、显式覆盖、防漂移、后端校验。"""
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import Settings


def test_paths_derived_from_data_dir(tmp_path, monkeypatch):
    # 数据路径单一来源：设置 data_dir 后 qdrant/sqlite 自动跟随
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.qdrant_path == tmp_path / "qdrant"
    assert s.database_url == f"sqlite:///{tmp_path / 'registry.db'}"


def test_explicit_paths_override_derivation():
    s = Settings(data_dir="data", qdrant_path="custom/qd", database_url="postgresql://x/y")
    assert s.qdrant_path == Path("custom/qd")
    assert s.database_url == "postgresql://x/y"


def test_unknown_rag_env_var_fails_fast(monkeypatch):
    # 拼写错误的环境变量不允许静默漂移（显式扫描 os.environ 拒绝）
    monkeypatch.setenv("RAG_LLM_APIKEY", "typo")  # 正确名应为 RAG_LLM_API_KEY
    with pytest.raises(ValidationError):
        Settings()


def test_openai_backend_requires_api_key():
    with pytest.raises(ValueError):
        Settings(embedding_backend="openai", openai_api_key="")
    s = Settings(embedding_backend="openai", openai_api_key="k")
    assert s.openai_api_key == "k"


def test_unknown_embedding_backend_raises():
    with pytest.raises(ValueError):
        Settings(embedding_backend="nope")


def test_defaults_align_with_architecture():
    # 审计 AUD-08：默认端点必须 vLLM 口径（9001），不再指向不存在的 Ollama 11434
    s = Settings()
    assert s.llm_base_url.endswith(":9001/v1")
    assert s.llm_model == "Qwen3-8B-AWQ"
    assert s.hnsw_m == 16 and s.hnsw_ef_construct == 200 and s.hnsw_ef == 256
    assert s.chunk_size == 512 and s.chunk_overlap == 64
    assert s.retrieval_top_k == 50 and s.rerank_top_k == 8
