# LocalRAGServer

本地大型 RAG 服务：供 Agent 调用的检索与生成基础设施。技术架构见 [docs/architecture.md](docs/architecture.md)。

当前状态：**Phase 3 · 服务化**（MCP 双 transport + ACL 强制层 + API Key 管理 + 限流/审计 + 探针；安全审查进行中）。

## 快速开始

```bash
# 1. 安装依赖（走代理）
export HTTP_PROXY=http://localhost:7897 HTTPS_PROXY=http://localhost:7897
uv sync --extra dev

# 2. 配置（默认 stub 嵌入后端，零模型依赖即可跑通）
cp .env.example .env

# 3. 启动
uv run uvicorn apps.api.main:create_app --factory --host 127.0.0.1 --port 8000
```

## 嵌入与 LLM 后端

| 后端 | 配置 | 说明 |
|---|---|---|
| `stub` | `RAG_EMBEDDING_BACKEND=stub` | 确定性哈希向量，仅开发/测试（默认） |
| `local` | `uv sync --extra embed` + `RAG_EMBEDDING_BACKEND=local` | 本机加载 bge-m3（GPU 自动选择） |
| `tei` | `RAG_EMBEDDING_BACKEND=tei` + `RAG_TEI_URL` | 远端 TEI 服务（生产推荐） |
| LLM | `RAG_LLM_BASE_URL` / `RAG_LLM_MODEL` | 任意 OpenAI 兼容端点（vLLM / Ollama / llama.cpp） |

## API 示例

```bash
# 创建知识库
curl -X POST http://127.0.0.1:8000/api/v1/kb \
  -H "Content-Type: application/json" \
  -d '{"name": "技术文档", "kb_type": "document"}'

# 上传文档（同步摄取，返回状态 ready 即已可检索）
curl -X POST http://127.0.0.1:8000/api/v1/kb/{kb_id}/documents \
  -F "file=@paper.pdf"

# 检索（只拿 chunk，不生成）
curl -X POST http://127.0.0.1:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "量子比特", "kb_id": "{kb_id}", "top_k": 5}'

# RAG 生成（OpenAI 兼容；rag_kb_id 为空时纯 LLM 透传）
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "什么是量子比特？"}], "rag_kb_id": "{kb_id}"}'
```

响应统一信封：`{success, data, error, meta}`；`/v1/chat/completions` 为 OpenAI 兼容格式，消息附带 `citations` 扩展字段（chunk → 来源文档 → 分数）。

## 测试

```bash
uv run pytest            # 全部 173 个测试（含真实 Qdrant 本地模式集成测试）
uv run pytest --cov      # 覆盖率报告（门禁 ≥80%，当前 93%）
uv run python scripts/smoke.py        # 全链路冒烟（上传→检索→RAG 拒答→删除）
uv run python -m eval.run_retrieval   # 评测集检索回归（recall@10 / MRR@10）
```

## Phase 1 · 异步摄取

```bash
# 1. 启动 worker（Windows：pool=solo；另开终端）
uv run python scripts/worker.py

# 2. 批量导入目录（幂等键 (kb_id, content_hash)；10 万级文档按此路径）
uv run python scripts/import_docs.py --kb <kb名称> --dir <目录>

# 3. URL 摄取（SSRF 五层防护内置；返回 job_id）
curl -X POST http://127.0.0.1:8000/api/v1/kb/{kb_id}/documents/url \
  -H "Authorization: Bearer $RAG_API_KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'

# 4. 吞吐基准（报告落 docs/perf/；真实数据实测 2850 文档/小时）
uv run python scripts/bench_ingest.py --dir <目录> --limit 100
```

状态机契约见 docs/design/ingest-state-machine.md；worker 形态与代理决策见 ADR-002。

## Phase 3 · MCP 与多租户

```bash
# 1. MCP stdio（Claude Code 配置，.mcp.json）
{"local-rag": {"command": "uv", "args": ["run", "python", "scripts/mcp_stdio.py"]}}

# 2. 签发受限 Key（仅 kb 白名单；明文仅显示一次）
uv run python -c "
from core.config import get_settings
from core.storage.registry import Registry
r = Registry(get_settings().database_url)
record, raw = r.create_api_key('agent-key', ['<kb_id>'])
print('API Key:', raw)
"

# 3. 探针
curl http://127.0.0.1:8000/healthz   # 存活
curl http://127.0.0.1:8000/readyz   # 依赖逐项连通性（database/qdrant 关键）
```

ACL 强制点与 Key 生命周期见 docs/design/acl-enforcement.md；限流本地化决策见 ADR-005。

## Windows 本机 GPU（Spike 实测，见 docs/spike/sm75-matrix.md）

| 组件 | 安装/运行 | 实测（RTX 2080 Ti） |
|---|---|---|
| 嵌入 | `uv pip install "torch==2.9.1" --index https://download.pytorch.org/whl/cu126` 后 `uv sync --extra embed` | bge-m3 **141 条/s**，驻留 ≈888MB |
| 生成 | llama.cpp b10427（CUDA 12.4 钉版）+ Qwen3-8B-Q4_K_M（`scripts/spike/download_*.py` 下载） | **解码 29.7 t/s**；服务形态用 llama-server（OpenAI 兼容，9001） |

注意：本机 uv 0.11.13 暂不读 pyproject 的索引配置（`[[tool.uv.index]]`），GPU torch 必须用上表 `uv pip install --index` 工作区方案（Phase 1 深查）；Windows PyPI 默认 torch 为 +cpu 版。生产嵌入走 TEI（`turing-` 标签镜像，Linux），见 ADR-001。

## 目录结构

```
apps/api/        # FastAPI：REST + OpenAI 兼容（routes: kb/documents/search/chat）
core/
├── config.py    # 全局配置（RAG_* 环境变量）
├── ingest/      # 解析器（txt/md/pdf）、分块器
├── retrieval/   # 嵌入后端（stub/local/tei/openai）、检索服务
├── generation/  # OpenAI 兼容 LLM 客户端、RAG 消息组装
└── storage/     # 向量库（Qdrant 本地模式/内存）、注册表（SQLite→Postgres）
tests/           # pytest（零网络依赖：假 LLM 服务 + 内存向量库）
docs/            # 架构文档与渲染页
```

## 路线图

Phase 0 MVP ✅ → Phase 1 摄取管线（MinerU/PaddleOCR、Celery 异步、批量导入）→ Phase 2 检索增强（混合检索/RRF、重排、parent-child、代码检索）→ Phase 3 服务化（MCP、认证限流）→ Phase 4 平台化（Web 管理端、监控）→ Phase 5 深化（评估闭环、网页增量、GraphRAG）。
