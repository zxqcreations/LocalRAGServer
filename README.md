# LocalRAGServer

本地大型 RAG 服务：供 Agent 调用的检索与生成基础设施。

- 技术架构：[docs/architecture.md](docs/architecture.md)（v2.0）
- **用户使用文档：[docs/user-guide.md](docs/user-guide.md)**
- 质量体系：[docs/quality.md](docs/quality.md)（9 项门禁 + 阶段映射）

当前状态：**v1.0.0**（2026-08-16 发布）——Phase 0-6 全部达成：
安全审计 / 备份恢复 / SLO 压测（万级 P95 160ms）/ 故障演练 / 发布回滚 /
Web 管理端（含 KB 全生命周期管理、文档上传、URL 订阅、ID 确认删除）/ RAGAS 评测闭环 / URL 订阅爬取。

开发中：**v1.2.0** — 知识库管理前端增强（KB CRUD / 详情页 / 文档上传 / URL 订阅 UI）。

## 快速开始

```bash
# 1. 安装依赖（走代理时先 export HTTP_PROXY/HTTPS_PROXY）
uv sync --extra dev

# 2. 配置（至少设置 RAG_API_KEY；默认 stub 嵌入零模型依赖可跑通）
cp .env.example .env

# 3. 启动
uv run uvicorn apps.api.main:create_app --factory --host 127.0.0.1 --port 8000
uv run python scripts/worker.py --beat          # 摄取 worker + URL 订阅调度
```

详细步骤（llama-server/vLLM 部署 / 管理端 / 运维）见 [docs/user-guide.md](docs/user-guide.md)。

## 接入方式

| 方式 | 说明 |
|---|---|
| OpenAI 兼容 REST | `/v1/chat/completions`（rag_kb_id 扩展字段）+ `/api/v1/*` 检索/文档 |
| MCP | stdio transport 5 工具（v1.0；streamable HTTP 见 ADR-006，v1.1） |
| Web 管理端 | Vue3 六模块（登录/**知识库管理**（含创建/编辑/删除 + KB 详情：上传文档/URL订阅/文档列表）/调试台/Key/监控/评估面板） |

## API 示例

```bash
# 创建知识库
curl -X POST http://127.0.0.1:8000/api/v1/kb \
  -H "Authorization: Bearer $RAG_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "技术文档", "kb_type": "document"}'

# 上传文档（同步摄取，返回状态 ready 即已可检索）
curl -X POST http://127.0.0.1:8000/api/v1/kb/{kb_id}/documents \
  -H "Authorization: Bearer $RAG_API_KEY" -F "file=@paper.pdf"

# 检索（只拿 chunk，不生成）
curl -X POST http://127.0.0.1:8000/api/v1/search \
  -H "Authorization: Bearer $RAG_API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "量子比特", "kb_id": "{kb_id}", "top_k": 5}'

# RAG 生成（OpenAI 兼容；rag_kb_id 为空时纯 LLM 透传）
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $RAG_API_KEY" -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "什么是量子比特？"}], "rag_kb_id": "{kb_id}"}'
```

## 文档索引

| 分类 | 文档 |
|---|---|
| 使用 | user-guide.md（本项目的完整使用手册） |
| 运维 runbook | backup-restore / failure-drill / release-rollback / qdrant-server / windows-linux-migration |
| 设计 | architecture.md · design/（acl-enforcement、ingest-state-machine、qdrant-hybrid-migration、tei-deployment-options、web-admin-auth、structlog-integration、ragas-eval、url-crawler、phase6-plan） |
| 决策 | adr/001-006 · spike/sm75-matrix.md |
| 过程 | audit/（30 轮路线对齐审计）· perf/（压测/评测报告） |
