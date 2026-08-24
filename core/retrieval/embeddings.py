"""嵌入后端抽象与工厂。

- stub  : 确定性哈希向量，零依赖，测试/无模型开发用
- local : 本机 sentence-transformers（GPU 自动选择，需 embed extra）
- tei   : 远端 TEI 服务（生产推荐）
- openai: 任意 OpenAI 兼容 /embeddings 端点
"""
import hashlib
import math
from typing import Protocol, cast, runtime_checkable

import httpx


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class StubEmbedder:
    """确定性 n-gram 哈希嵌入（审计 F5 语义化升级）。

    同一文本恒等向量；相似文本共享字符 n-gram → 余弦高；
    无关文本余弦低。混合 2/3-gram 平衡短锚点召回与碰撞噪声。
    作为检索链路测试的地基，具备真实相似性结构。仅用于测试与无模型开发。
    """

    def __init__(self, dim: int = 1024, ngram_sizes: tuple[int, ...] = (3,)) -> None:
        self.dim = dim
        self._ns = ngram_sizes

    def _grams(self, text: str) -> list[str]:
        if not text:
            return []
        grams: list[str] = []
        for n in self._ns:
            if len(text) < n:
                grams.append(text)
            else:
                grams.extend(text[i : i + n] for i in range(len(text) - n + 1))
        return grams

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for gram in self._grams(text):
                h = int(hashlib.sha256(gram.encode("utf-8")).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vectors.append([x / norm for x in vec])
        return vectors


class LocalEmbedder:
    """本机加载 sentence-transformers 模型（自动选择 GPU）；需 `uv sync --extra embed`。

    依赖模型下载与 GPU 环境，自动化测试不覆盖（pragma: no cover），
    由 Phase 1 的 GPU 集成验证覆盖。
    """

    def __init__(self, model_name: str) -> None:  # pragma: no cover
        # 延迟导入：未装 embed extra 时抛 ImportError（可选项依赖）
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

        self._model = SentenceTransformer(model_name)
        # type: ignore — torch/sentence-transformers stub 联动使本类分析推迟（LocalEmbedder*），
        # 运行时语义正确；本类为 GPU 手动验证路径（pragma: no cover）
        self.dim: int = getattr(
            self._model, 'get_embedding_dimension',
            lambda: self._model.get_sentence_embedding_dimension()
        )()  # type: ignore

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        # encode 返回类型随输入形式变化（typeshed 声明为联合类型），显式收窄
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [cast(list[float], v.tolist()) for v in vectors]


class TeiEmbedder:
    """远端 TEI（text-embeddings-inference）服务。"""

    def __init__(self, url: str, dim: int) -> None:
        # trust_env=False：目标为本地推理服务，绝不走系统代理
        self._client = httpx.Client(base_url=url.rstrip("/"), trust_env=False)
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post("/embed", json={"inputs": texts, "normalize": True})
        resp.raise_for_status()
        vectors = resp.json()
        if any(len(v) != self.dim for v in vectors):
            raise RuntimeError(f"TEI 返回维度与配置不符：期望 {self.dim}")
        return vectors


class OpenAIEmbedder:
    """任意 OpenAI 兼容 /embeddings 端点。"""

    def __init__(self, base_url: str, api_key: str, model: str, dim: int) -> None:
        # 归一化 base_url：兼容带/不带 /v1 前缀，统一按 /v1/embeddings 请求
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v1"):
            normalized = normalized[: -len("/v1")]
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # trust_env=False：目标为本地推理服务，绝不走系统代理
        self._client = httpx.Client(base_url=normalized, headers=headers, trust_env=False)
        self._model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post("/v1/embeddings", json={"input": texts, "model": self._model})
        resp.raise_for_status()
        vectors = [item["embedding"] for item in resp.json()["data"]]
        if any(len(v) != self.dim for v in vectors):
            raise RuntimeError(f"嵌入端点返回维度与配置不符：期望 {self.dim}")
        return vectors


def build_embedder(settings) -> Embedder:
    backend = settings.embedding_backend
    if backend == "stub":
        return StubEmbedder(settings.embedding_dim)
    if backend == "local":
        return LocalEmbedder(settings.embedding_model)
    if backend == "tei":
        return TeiEmbedder(settings.tei_url, settings.embedding_dim)
    if backend == "openai":
        return OpenAIEmbedder(
            settings.openai_base_url,
            settings.openai_api_key,
            settings.embedding_model,
            settings.embedding_dim,
        )
    raise ValueError(f"未知嵌入后端：{backend}（可选 stub | local | tei | openai）")
