# LocalRAGServer 用户使用文档

> 版本：v1.2.0-dev · 面向：部署者 / Agent 接入者 / 管理端使用者

## 1. 快速开始

### 1.1 环境要求

- Windows 11（或 Linux，见 §7.5 迁移清单）
- Python 3.13 + [uv](https://docs.astral.sh/uv/)
- 可选：NVIDIA GPU 8GB+（嵌入/生成加速；无 GPU 可用 stub/远程端点）
- 可选：PostgreSQL（生产推荐；开发默认 SQLite 免安装）

### 1.2 安装与配置

```bash
# 依赖安装（走代理时先 export HTTP_PROXY/HTTPS_PROXY）
uv sync --extra dev

# 配置
cp .env.example .env
# 编辑 .env：至少设置 RAG_API_KEY（主 Key，业务接口的强制凭证）
```

### 1.3 启动

```bash
# API 服务
uv run uvicorn apps.api.main:create_app --factory --host 127.0.0.1 --port 8000

# worker（摄取任务消费；--beat 同时启动 URL 订阅调度）
uv run python scripts/worker.py --beat

# 首次启动会在 data/admin_initial_password 生成初始管理密码（日志可见），
# 登录管理端后强制修改（明文文件改密后自动销毁）
```

### 1.4 验证

```bash
curl http://127.0.0.1:8000/healthz        # 存活
curl http://127.0.0.1:8000/readyz         # 就绪（database/qdrant 关键依赖）
uv run python scripts/smoke.py            # 全链路冒烟（上传→检索→生成→删除）
```

## 2. 配置参考（RAG_* 环境变量全表）

| 变量 | 默认 | 说明 |
|---|---|---|
| RAG_APP_NAME / RAG_HOST / RAG_PORT | LocalRAGServer / 127.0.0.1 / 8000 | 服务标识与绑定 |
| RAG_DATA_DIR | data/ | 数据根目录（SQLite/Qdrant/ingest_work 派生于此） |
| **RAG_API_KEY** | 空（fail-closed） | 主 Key——空则业务接口全部 503 |
| RAG_DATABASE_URL | 派生 sqlite:/// | 生产可切 `postgresql+psycopg://...` |
| RAG_QDRANT_URL | 空（本地模式） | 生产设 `http://127.0.0.1:6333`（HNSW，见 §6.3） |
| RAG_QDRANT_PATH | data/qdrant | 本地模式存储路径 |
| RAG_HNSW_M / _EF_CONSTRUCT / _EF | 16/200/256 | HNSW 参数（server 模式生效） |
| RAG_EMBEDDING_BACKEND | stub | stub（开发）\| local（本机 bge-m3）\| tei \| openai |
| RAG_EMBEDDING_MODEL / _DIM | BAAI/bge-m3 / 1024 | 嵌入模型与维度 |
| RAG_TEI_URL / RAG_OPENAI_BASE_URL | 127.0.0.1:9002 | 远端嵌入端点 |
| RAG_LLM_BASE_URL / _MODEL / _API_KEY | 127.0.0.1:9001/v1 · Qwen3-8B-AWQ | OpenAI 兼容生成端点 |
| RAG_URL_ALLOWLIST | 空 | URL 摄取域名白名单（逗号分隔；空=任意公网，解析后 IP 校验兜底） |
| RAG_URL_FETCH_MAX_BYTES / _REDIRECTS / _TIMEOUT | 5MB/3/15s | URL 抓取防护 |
| RAG_URL_FETCH_ALLOW_LOOPBACK | false | **生产必须 false**（SSRF 防护） |
| RAG_CELERY_BROKER_URL | filesystem:// | 生产切 Redis（ADR-002） |
| RAG_REDIS_URL | 空（进程内限流） | 生产设 `redis://…`：限流令牌桶跨进程一致（ADR-005 Phase 6；Redis 不可用自动回退内存，fail-open） |
| RAG_MAX_UPLOAD_MB / MAX_PDF_PAGES | 200/1000 | 上传防护 |
| RAG_REFUSAL_THRESHOLD | 0.25 | 检索最高分低于此值 → 拒答 |
| RAG_CHUNK_SIZE / _OVERLAP | 512/64 | 分块策略 |
| RAG_RETRIEVAL_TOP_K / SEARCH_TOP_K / RERANK_TOP_K | 50/5/8 | 检索参数 |
| RAG_RRF_K | 60 | RRF 融合参数 |
| RAG_RERANK_BACKEND | off | off \| tei（重排） |

> 未知 RAG_* 变量启动即报错（fail-fast 防拼写漂移）。

## 3. Web 管理端

访问 `http://127.0.0.1:5173`（开发）或生产构建产物。首次登录用初始密码（§1.3），强制修改后进入：

| 模块 | 功能 |
|---|---|
| 知识库管理 | 创建 / 编辑 / 删除（ID 确认保护）；按 KB 维度查看统计（文档数、碎片数、失败数）；点击进入详情页 |
| KB 详情页 | 元数据卡片（名称/类型/ID/创建时间/描述/统计）；文档上传（支持拖拽选择）；文档列表（标题/状态徽章/碎片数/错误）；URL 订阅管理（新增/启用-禁用/删除） |
| 检索调试台 | 三阶段调试（dense/sparse/融合）+ 人工标注（沉淀为评测集） |
| API Key | 签发（明文仅显示一次）/吊销（KB 级 ACL） |
| 系统监控 | 指标分位数 + 审计日志 |
| 评估面板 | 人工标注列表 |

角色两档：**admin**（全部操作）/ **readonly**（只读，无任何变更与敏感读）。
服务端强制首次改密（改密前业务端点 403）。

**安全注意：** KB 和文档的删除操作需弹窗确认——在文本框中输入完整的 ID（32 位十六进制字符），输入匹配后按钮才可点击。这是防误删的核心机制。

## 4. 知识库与文档管理

### 4.1 在 Web 管理端操作 KB

1. **登录管理端**：访问 `http://127.0.0.1:5173`，使用初始密码登录后强制改密。
2. **创建 KB**：点击「知识库管理」→「创建知识库」→填写名称、类型（document/code/web）、简介→确认。
3. **查看统计**：列表中实时显示每个 KB 的文档数、碎片总数、失败任务数（红色 badge）。
4. **进入详情**：点击 KB 名称进入详情页，可查看元数据、上传文档、管理 URL 订阅。
5. **删除 KB**：需在弹窗中精确输入 KB ID（32 位 hex），匹配后确认。

### 4.2 上传文档

#### 通过 Web 管理端

在 KB 详情页点击「上传文档」按钮，选择文件后自动触发同步摄取管线（解析→分块→向量化→入库）。完成后刷新页面即可看到新文档及其状态徽章（就绪/失败/进行中）。

#### 通过 REST API

```bash
# multipart/form-data（需 API Key）
curl -X POST http://127.0.0.1:8000/api/v1/kb/{kb_id}/documents \
  -H "Authorization: Bearer $RAG_API_KEY" -F "file=@paper.pdf"

# 或 JSON + base64（通过 admin 端点，需 Cookie+CSRF）
curl -X POST http://127.0.0.1:8000/admin/api/kb/{kb_id}/documents/upload-json \
  -H "Cookie: rag_admin_session=<SESSION>" \
  -H "X-CSRF-Token: <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"filename": "paper.pdf", "data": "<base64-encoded-content>"}'
```

支持 PDF/Word/Markdown/代码/网页/EPUB 等；魔数与扩展名双重校验；
单文件 ≤200MB、PDF ≤1000 页；同内容重复上传幂等（返回已有文档）。

### 4.3 URL 摄取（单次）

```bash
curl -X POST http://127.0.0.1:8000/api/v1/kb/{kb_id}/documents/url \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/doc"}'
```

SSRF 五层防护全程生效（协议白名单/全 IP 解析校验/逐跳重验/大小上限/固定 IP 连接）。

### 4.4 URL 订阅爬取（增量）

#### 通过 Web 管理端

在 KB 详情页的「URL 订阅」区域，点击「新增订阅」→填入 URL 和抓取间隔（1h/24h/7天）→确认。
之后可在订阅列表中启用/禁用/删除单个订阅。

#### 通过管理端 API（admin 角色）

```bash
# 创建订阅
curl -X POST http://127.0.0.1:8000/admin/api/subscriptions \
  -H "Cookie: rag_admin_session=<SESSION>" \
  -H "X-CSRF-Token: <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"kb_id": "<kb_id>", "url": "https://example.com/page", "interval_hours": 24}'

# 切换启用/禁用
curl -X POST http://127.0.0.1:8000/admin/api/subscriptions/<sub_id>/toggle \
  -H "Cookie: ..." -H "X-CSRF-Token: ..." \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

worker `--beat` 每 10 分钟扫描到期订阅：内容哈希变化 → 自动重索引（旧版保留）；
未变化零成本。抓取失败自动退避并记录。

## 5. API 使用

### 5.1 认证

```
Authorization: Bearer <API_KEY>
```

- 主 Key（RAG_API_KEY）：全权限（含创建知识库）
- 管理端签发的 Key：KB 级 ACL（`["*"]` 或 KB id 列表）；越权显式 403
- 限流：per-IP 30/0.5s + per-Key 120/2s（超出 429）
- 统一信封：`{"success": bool, "data": ..., "error": {"code", "message"} | null, "meta": ...}`
- 响应头 `X-Trace-Id`：与结构化日志 trace 关联（排障用）

### 5.2 检索

```bash
curl -X POST http://127.0.0.1:8000/api/v1/search \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"query": "波导放大器增益", "kb_id": "{kb_id}", "top_k": 5}'
```

混合检索（dense bge-m3 + BM25 → RRF 融合）→ 可选重排 → top-N 截断。
返回含 chunk 原文 + 文档标题 + 分数。

### 5.3 RAG 生成（OpenAI 兼容）

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "什么是量子比特？"}],
    "rag_kb_id": "{kb_id}",
    "rag_top_k": 3,
    "stream": false
  }'
```

- `rag_kb_id` 为空 → 纯 LLM 透传
- 检索最高分 < `RAG_REFUSAL_THRESHOLD` → 拒答（不调用 LLM）
- 返回 citations（文档标题/分数/来源）
- `stream: true` → SSE 流式

### 5.4 管理端 API（session 认证）

所有 `/admin/api/*` 端点使用 Cookie 会话（`rag_admin_session` HttpOnly Cookie）+ CSRF token（`X-CSRF-Token` 头）。
与 API Key Bearer 通道完全隔离（Bearer 请求到达管理端路由时会被 403 拒绝）。

| Method | Path | 角色 | 说明 |
|--------|------|------|------|
| POST | `/login` | public | 登录（返回 `username`/`role`/`csrf_token`） |
| POST | `/change-password` | authed | 修改密码 |
| GET | `/me` | authed | 当前用户信息 + CSRF token |
| GET | `/kb` | any | 增强版 KB 列表（含 doc_count/chunk_count/failed_count） |
| POST | `/kb` | admin | 创建知识库 |
| PUT | `/kb/{id}` | admin | 更新知识库（部分更新） |
| DELETE | `/kb/{id}` | admin | 级联删除 KB |
| GET | `/kb/stats` | any | 全量 KB 统计数据 |
| GET | `/kb/{id}` | any | 单 KB 详情（元数据 + 统计） |
| POST | `/kb/{id}/documents/upload-json` | admin | JSON+base64 文件上传 |
| GET | `/kb/{id}/documents` | any | KB 下文档列表 |
| DELETE | `/kb/{id}/documents/{doc_id}` | admin | 删除文档 |
| POST | `/keys` | admin | 签发 API Key |
| GET | `/keys` | admin | 列出 API Key |
| DELETE | `/keys/{id}` | admin | 吊销 Key |
| GET | `/metrics` | any | 系统指标快照 |
| GET | `/audit?limit=N` | any | 审计日志（上限 500） |
| POST | `/annotations` | admin | 人工标注 |
| GET | `/annotations?kb_id=...` | admin | 标注列表 |
| POST | `/search-debug` | any | 三阶段检索调试 |
| POST | `/subscriptions` | admin | 创建 URL 订阅 |
| GET | `/subscriptions?kb_id=...` | any | 订阅列表 |
| POST | `/subscriptions/{id}/toggle` | admin | 启用/禁用订阅 |
| DELETE | `/subscriptions/{id}` | admin | 删除订阅 |

### 5.5 MCP 接入（Agent 直连）

五个工具：`search_knowledge` / `list_knowledge_bases` / `ask` / `ingest_document` /
`get_document_status`（工具描述内嵌 KB 目录，供 Agent 自动选择）。

**stdio（本机，Claude Code 即配即用）**：

```json
{ "mcpServers": { "local-rag": {
  "command": "uv", "args": ["run", "python", "scripts/mcp_stdio.py"]
} } }
```

stdio 通道以本机信任边界授予主 Key 全权限语义，支持本地文件摄取。

**streamable HTTP（v1.1，远程 Agent / 多机接入）**：

```text
端点：POST http://<host>:8000/mcp   （JSON-RPC 2.0，Content-Type: application/json）
认证：Authorization: Bearer <API_KEY>（与 REST 同一强制点；空 Key fail-closed）
会话：首次 POST initialize 后，响应头 Mcp-Session-Id 需随后续请求回传；
      会话结束 DELETE /mcp（带同一 Session-Id 头）
```

```bash
# 1) 初始化（建立会话）
curl -X POST http://127.0.0.1:8000/mcp -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
       "protocolVersion":"2025-03-26","capabilities":{},
       "clientInfo":{"name":"my-agent","version":"0.1"}}}'
# → 响应头 Mcp-Session-Id: <SID>

# 2) 调用工具（带会话头）
curl -X POST http://127.0.0.1:8000/mcp -H "Authorization: Bearer $KEY" \
  -H "Mcp-Session-Id: <SID>" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
       "name":"search_knowledge","arguments":{"query":"波导增益","kb":"<kb_id>"}}}'
```

HTTP 通道语义：

- 权限与 REST 完全同源：Key 的 KB 级 ACL 逐请求生效（越权 → 工具级错误，
  显式「无权限」而非空结果）；`list_knowledge_bases` 只显示有权限的 KB
- `ingest_document` 在 HTTP 通道被拒绝（仅 stdio 本机通道可用，防远程任意文件读取）
- `get_document_status` 校验任务归属 KB 在调用方 ACL 内
- 每次工具调用落审计（`mcp_*` 动作码，含 Key 主体与 trace_id）
- 限流/请求体上限与 REST 同规则；对外暴露需 TLS 前置（phase6-plan 附录 A）

## 6. 模型与性能部署

### 6.1 嵌入（bge-m3 本机 GPU）

```bash
uv pip install torch --index https://download.pytorch.org/whl/cu126   # 实测 Turing 可用
RAG_EMBEDDING_BACKEND=local   # 启动时加载 BAAI/bge-m3（HF 缓存离线可用）
```

### 6.2 生成（llama-server）

```bash
models/llamacpp/llama-server.exe -m models/gguf/Qwen3-8B-Q4_K_M.gguf \
  --host 127.0.0.1 --port 9001 -ngl 40 -c 8192
RAG_LLM_BASE_URL=http://127.0.0.1:9001/v1
```

实测 29.7 t/s（RTX 2080 Ti 11GB）。模型文件经 models/MANIFEST.json 管理，
旧版本保留 ≥72h（回滚契约）。

### 6.3 向量库（生产形态）

```bash
models/qdrant-server/qdrant.exe --uri http://127.0.0.1:6333
RAG_QDRANT_URL=http://127.0.0.1:6333
```

**必须切换**：本地嵌入模式为 brute-force，万级文档 P95 749ms 超限；
server 模式（HNSW + chunk 缓存）实测 **160ms 达标**（详见 runbooks/qdrant-server.md）。

### 6.4 SLO 基准

| 规模 | P95 | 口径 |
|---|---|---|
| 200 文档 | 18.4ms | stub 嵌入 |
| 1 万文档 | **160.1ms** | Qdrant server + stub（目标 <500ms 达标） |
| 摄取吞吐 | 3928 文档/小时 | 管线开销口径（10 万级外推 25h，多 worker 分摊） |

## 7. 运维手册

| 场景 | 文档 |
|---|---|
| 备份与恢复 | docs/runbooks/backup-restore.md（两场景演练脚本） |
| 故障处理 | docs/runbooks/failure-drill.md（降级矩阵） |
| 发布与回滚 | docs/runbooks/release-rollback.md（三路径 + 24h 观察期） |
| TLS 暴露 | docs/design/phase6-plan.md 附录 A（九项前置条件） |
| Linux 迁移 | docs/runbooks/windows-linux-migration.md |

常用操作：

```bash
curl http://127.0.0.1:8000/readyz      # 依赖逐项就绪（关键依赖 down → 503 + 告警）
uv run python scripts/drill_backup_restore.py   # 备份恢复演练
uv run python scripts/drill_failures.py         # 故障演练
uv run python -m eval.run_retrieval --check-baseline  # 检索回归门禁
```

## 8. 评测与质量

- 检索回归：recall@10=0.880 / MRR@10=0.790（基线门禁容差 0.05）
- RAGAS 四指标（独立评判模型 Qwen2.5-7B-Instruct）：
  faithfulness 0.925 / answer_relevancy 0.864 / context_precision 0.847 / context_recall 1.0
- 评测集版本化：跨版本结果不可比（门禁强制）
- 全量评测：`uv run python -m eval.ragas_runner --check-baseline`（需 llama-server）

## 9. 故障排查

| 现象 | 处理 |
|---|---|
| 业务接口 503 AUTH_UNCONFIGURED | 未设 RAG_API_KEY（fail-closed 设计） |
| readyz 503 | database/qdrant 关键依赖 down——查日志 + 备份恢复 runbook |
| Chat 502 llm_unavailable | LLM 端点不可达（非关键降级，检索不受影响） |
| 上传 415 | 格式不支持或扩展名与内容魔数不符 |
| URL 摄取 403 ssrf_blocked | 目标解析到非公网地址（防护拦截） |
| 管理端 403 password_change_required | 首次登录必须先改密（服务端强制） |
| 检索慢（万级） | 确认 RAG_QDRANT_URL 已切 server 模式（本地模式 brute-force） |
| 未知 RAG_* 报错 | 环境变量拼写错误（fail-fast 提示） |

## 10. 快速命令索引

```bash
uv run uvicorn apps.api.main:create_app --factory --port 8000   # API
uv run python scripts/worker.py --beat                          # worker + 订阅调度
uv run python scripts/import_docs.py --kb <名> --dir <目录>      # 批量导入
uv run python scripts/prepare_eval_kbs.py                       # 评测 KB 准备
uv run python scripts/bench_search.py --docs 10000 --fast --server http://127.0.0.1:6333  # SLO 压测
npx playwright test                                             # 管理端 E2E（apps/web）
```
