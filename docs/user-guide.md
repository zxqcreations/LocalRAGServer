# LocalRAGServer 用户使用文档

> 版本：v1.0.0（2026-08-16）· 面向：部署者 / Agent 接入者 / 管理端使用者

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
| 知识库 | 创建/列表/详情 |
| 检索调试台 | 三阶段调试（dense/sparse/融合）+ 人工标注（沉淀为评测集） |
| API Key | 签发（明文仅显示一次）/吊销（KB 级 ACL） |
| 系统监控 | 指标分位数 + 审计日志 |
| 评估面板 | 人工标注列表 |

角色两档：**admin**（全部操作）/ **readonly**（只读，无任何变更与敏感读）。
服务端强制首次改密（改密前业务端点 403）。

## 4. 知识库与文档管理

### 4.1 上传文档

支持 PDF/Word/Markdown/代码/网页/EPUB 等；魔数与扩展名双重校验；
单文件 ≤200MB、PDF ≤1000 页；同内容重复上传幂等（返回已有文档）。

### 4.2 URL 摄取（单次）

```bash
curl -X POST http://127.0.0.1:8000/api/v1/kb/{kb_id}/documents/url \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/doc"}'
```

SSRF 五层防护全程生效（协议白名单/全 IP 解析校验/逐跳重验/大小上限/固定 IP 连接）。

### 4.3 URL 订阅爬取（增量）

管理端「订阅」API（admin 角色）创建订阅（URL + 周期）；worker `--beat`
每 10 分钟扫描到期订阅：内容哈希变化 → 自动重索引（旧版保留）；
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

### 5.4 管理端 API

`/admin/api/*`：登录（Cookie 会话 + CSRF token）/ 改密 / 登出 / KB / API Key /
订阅 / 标注 / 审计 / 指标。管理端与 API Key 通道彻底隔离（Bearer 被显式拒绝）。

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
