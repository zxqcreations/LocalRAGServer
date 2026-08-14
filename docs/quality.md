# LocalRAGServer 企业级质量工作流

> 来源：ultracode 基线审计（11 代理：5 路审计 → 5 路对抗验证 → 1 路合成，2026-08-14）
> 本文档定义本项目的**不可妥协质量门**、**每轮路线对齐审计机制**与**缺陷治理制度**。

## 1. 质量门总纲（八项，永不豁免）

任何阶段合入代码必须通过：

| # | 门禁 | 工具 | 阈值 |
|---|---|---|---|
| 1 | 代码风格 | ruff（E/F/I/UP/B） | 零错误 |
| 2 | 类型检查 | pyright/mypy | 零错误 |
| 3 | 测试覆盖率 | pytest --cov | **≥80%（CI 强制拒绝合并）** |
| 4 | 安全静态扫描 | bandit + pip-audit | 零高置信告警 / 无高危 CVE |
| 5 | 代码审查 | code-reviewer / 人工 | 无 CRITICAL/HIGH 遗留 |
| 6 | 安全审查 | security-reviewer | **触发区强制**：认证/上传/DB/文件系统/外部 API/密钥管理 |
| 7 | 性能基准 | docs/perf/ 脚本 | 对照架构 §8.4 不劣化 |
| 8 | 架构一致性 | 每轮审计五查 | 无偏离（偏离须走 ADR） |

## 2. 阶段门禁（Phase 0-6）

> 每阶段四段式：进入标准 → 质量门 → 完成定义(DoD) → 交付物。
> 版本映射：P0→v0.1.0，P1→v0.2.0，P2→v0.3.0，P3→v0.4.0，P4→v0.5.0，P5→v0.6.0，P6→v1.0.0。

### Phase 0 · MVP（当前阶段）
- **进入**：git init + 受保护 main；环境决策 ADR 先行（生成后端 Turing 选型 Spike、免 Docker Qdrant 验证）；dev 工具链补齐；测试隔离基座；Python 版本口径统一
- **关键质量门**：最小认证骨架与第一批接口**同批交付**（fail-closed）；上传防护（UUID 对象键/魔数/大小上限）；冒烟脚本全链路；配置整改（见 §5 P0-3）
- **DoD**：/search + /chat 可用（统一信封+错误码）；覆盖率 ≥80% CI 强制；SM75 兼容矩阵 + 生成后端 ADR 获批；v0.1.0

### Phase 1 · 摄取管线
- **关键门**：状态机全转移表驱动测试；幂等键 = **(kb_id, content_hash)**（同文件可入多 KB、同 KB 内去重）；ZIP 容器预检（压缩比>100:1 拒绝/深度≤2）；Alembic 可逆迁移 + SQLite→PG 回滚演练；SSRF 防护与 URL 摄取**同批落地**；documents 表含 pipeline_version

### Phase 2 · 检索增强
- **关键门**：评测集 v1（≥50 条）进 CI（recall@k/MRR 不下降才可合并）；RRF 纯函数手算 fixture ≥3 组；拒答三分支 + LLM 零调用断言 + hard-negative；golden 向量/重排快照；P95<500ms 实测（不达标出 ADR）；批量回查 `WHERE id = ANY(...)` 禁止逐条点查

### Phase 3 · 服务化
- **关键门**：**ACL 强制点**：唯一入口 `resolve_allowed_kb_ids(key)`，kb_id 只由服务端推导、与 Key ACL 求交集，越权 403；跨 KB 泄漏集成测试矩阵进 CI；MCP 双 transport 实测；Key 慢哈希（argon2id/scrypt）+ 轮换/吊销；audit_logs 表；/healthz + /readyz

### Phase 4 · 平台化
- **关键门**：Web 管理端独立认证（初始密码强制改、HttpOnly/CSRF、RBAC 两档）；5 模块 Playwright E2E；trace_id 全链路验证；**告警规则集上线**（含备份失败告警）；标注 API 契约测试

### Phase 5 · 深化
- **关键门**：RAGAS 四项进 CI（judge 独立选型防自评偏置）；评测集版本化+污染隔离；压测报告与容量估算对照归档（差异逐条 ADR）；缺陷清场（无未关闭 CRITICAL/HIGH）；架构文档 v2.0 草案

### Phase 6 · 生产就绪（**审计新增阶段**）
- **关键门（生产就绪七项全绿）**：①安全审计（TLS/RBAC/审计流/密钥轮换/敏感内容策略）②备份+恢复演练（两场景 runbook 实测）③SLO 压测达标（满数据量 P95<500ms + 耗时分解）④故障演练（逐依赖击杀验证降级矩阵）⑤发布/回滚 runbook 演练（per-KB feature flag，旧模型保留 ≥72h）⑥灰度放量验证 ⑦Windows→Linux 迁移验收（同模型同 1000 文档对比）
- **DoD**：v1.0.0 发布（24h 观察期，P95 劣化>30% 即回滚）；架构文档 v2.0 正式批准

## 3. 路线对齐审计机制（每轮工作收尾强制执行）

**触发**：①每轮工作结束（审计记录缺失 = 本轮未完成）；②每阶段 DoD 判定前追加正式审计；③每周缺陷评审。

**五查**：
1. **范围对齐**：产出对照当前 Phase 交付物清单；是否蔓延（提前引入 Celery/MCP/Web/GraphRAG 等）
2. **技术对齐**：选型与架构 §3/§14 一致；新依赖在固定栈内；架构外选型必须 ADR
3. **架构一致性**：分层/状态机/数据模型/API 契约（统一信封+错误码）/安全基线
4. **规范对齐**：覆盖率 ≥80%、零告警、无硬编码密钥、文件<800行/函数<50行、安全审查记录
5. **交付物完整性**：对照 deliverables 逐项核对，缺失项明确责任人

**输出**：`docs/audit/YYYYMMDD-<轮次>.md`（本轮目标 / 实际完成 / 偏离项含理由 / 纠正措施 / 下轮输入）

**偏离治理**：发现偏离二选一——记 ADR（评审批准）或回退；架构文档变更必须重新批准（v2.0 机制）；阶段跳步视为蔓延；覆盖率 <80% 直接拒绝合并。

**Checkpoints**：C0~C6 里程碑（各阶段发布）；R1 每轮审计 / R2 每周缺陷评审 / R3 版本映射核对；T1 SM75 兼容矩阵复核 / T2 Linux 冒烟 / T3 迁移路径演练。

## 4. 缺陷分级与时效（S0-S3）

| 级 | 定义 | 时效 |
|---|---|---|
| S0 | 数据丢失/损坏、安全漏洞、服务全挂 | 2h 内确认，24h 内 hotfix 补丁 |
| S1 | 核心功能不可用/严重劣化 | 当日响应，3 工作日内修复 |
| S2 | 部分受损有绕过方案 | 当版本内修复或登记延期（审计中说明） |
| S3 | 文档/样式/改进建议 | 排期随下次发布 |

## 5. 基线审计结论与处置清单

### 5.1 结论
架构方向正确、脚手架骨架可用（对抗验证推翻 5 项误报：uv.lock、build-system、tests 目录均已存在）。但存在 **2 critical + 13 high** 缺口，集中在：硬件约束未吸收（Turing SM75）、企业级能力缺失（备份/版本管理/告警/CI/迁移）、安全排期滞后（认证/ACL）、测试基建未落地。

### 5.2 立即行动（P0-1 ~ P0-9）

| # | 行动 | 状态 |
|---|---|---|
| P0-1 | git init + 受保护 main + .gitignore 补漏（.coverage/models/.claude/settings.local.json 等） | **完成**（v0.1.0 已推送） |
| P0-2 | **硬件 Spike**：实机验证 vLLM 钉版 / llama.cpp GGUF / TEI turing- 三路径，出 SM75 兼容矩阵 + 生成后端 ADR | **完成**：sm75-matrix.md + ADR-001（llama.cpp 实测 29.7 t/s；vLLM 原生 Windows 不可行；TEI 留待 Linux） |
| P0-3 | 配置整改：LLM 默认端点改 9001 或 fail-fast；路径从 data_dir 派生；补检索参数（top_k/RRF/HNSW/chunk）；非 stub 后端启动校验 Key；Python 版本钉 3.13 | **完成**（含未知 RAG_* env fail-fast） |
| P0-4 | 测试基座：dev 补 pyright/bandit/pip-audit/pytest-asyncio；conftest autouse 隔离 fixture（env 强制 + cache_clear + 外联护栏）；stub 升级 n-gram 语义化 | **完成**（五门禁全零 + 语义化 stub + respx 护栏） |
| P0-5 | CI 三层流水线（PR 快速层 / main 回归层 / GPU nightly 层，Python 3.11+3.13 + Windows） | **完成**（.github/workflows/ci.yml；真实运行随推送） |
| P0-6 | 安全最小骨架：Bearer Key 中间件 + fail-closed + host 变更警告；上传校验（UUID 键/MIME 魔数/大小/MAX_PDF_PAGES）；错误码目录 + 全局异常处理器 | **完成** |
| P0-7 | 架构补「备份与灾备」「模型注册表+pipeline_version+双 collection 切换」章节 | **完成**（architecture.md v1.1/v1.2；pipeline_version 已实现） |
| P0-8 | eval/ 目录 + qa.jsonl 种子 ≥50 条（锚定 doc_id/chunk_id + is_hard）+ 评测集版本化方案 | **完成**（50 条锚定集 + 回归脚本 recall@10=0.880） |
| P0-9 | docs/audit/ 制度固化 + S0-S3 成文 + 冒烟脚本回归基线 | **完成**（13 轮审计记录 + 冒烟/评测回归基线） |

### 5.3 已验证发现登记册（按处置阶段）

| ID | 发现 | 级别 | 处置阶段 |
|---|---|---|---|
| ARC-001 | Turing SM75 vLLM 回归风险（v0.24.0 移除 FlashInfer，吞吐跌至 ~1 tok/s） | CRITICAL | P0 Spike + 版本钉扎 |
| ARC-002 | 备份/恢复与灾备缺失（无 RPO/RTO/演练） | CRITICAL | P0 设计定稿 / P6 落地 |
| F-01 | Phase 0 暴露接口但认证排 P3（无认证窗口） | HIGH | **P0 同批交付最小认证** |
| F-13 | KB ACL 强制点未定义（kb_id 裸传即全库泄露面） | HIGH | P0 设计定稿 / P3 落地 |
| ARC-004 | 11GB 实机与 Profile 不匹配、C 档数值矛盾 | HIGH | **P0 新增 Profile D** |
| ARC-003 | 模型版本管理/重索引策略缺失（pipeline_version） | HIGH | P1 字段 / P2 双 collection |
| ARC-005 | 多租户隔离 opt-in 而非 deny-by-default，无审计表 | HIGH | P3 强制层 + audit_logs |
| ARC-006 | 有指标无告警规则/SLO 定义 | HIGH | P4 告警闭环 / P6 SLO 压测 |
| ARC-007 | 无 CI/CD、灰度与回滚 | HIGH | P0 建 CI / P6 发布工程 |
| ARC-008 | SQLite→PG、Qdrant 本地→服务迁移方案缺失 | HIGH | P1 Alembic + 迁移 runbook |
| ARC-009 | Web 管理端无认证、MCP HTTP 无鉴权、无密钥轮换 | HIGH | P3/P4 落地 |
| ARC-010 | 无 /healthz/readyz、对账 job、DLQ、错误码目录 | HIGH | P0 错误码 / P3 探针 / P6 对账 |
| ARC-011 | 路线图缺生产就绪阶段 | HIGH | **P0 已补 Phase 6** |
| ARC-012 | 单机资源总账矛盾（容器 RAM 合计 >64GB 建议） | HIGH | P6 资源总账修订 |
| F-07 | 上传文件名未规范化（路径穿越） | HIGH | **P0 UUID 对象键** |
| F-09 | 恶意 PDF/超大文件无上限，ClamAV 可选 | HIGH | **P0 上限强制** |
| F-03 | Web 管理端无管理员认证设计 | HIGH | P4 落地 |
| F1 | config 全局单例破坏测试隔离 | HIGH | P0 conftest 隔离 |
| F9 | 评测集 Day 1 未落地（eval/ 为空） | HIGH | P0 种子 / P2 进 CI |
| F13 | 测试隔离基座缺失（无强制 conftest） | HIGH | P0 conftest |
| F16 | 无 CI 三层流水线 | HIGH | P0-5 |
| F2/F3/F4/F6/F10/F11/F12/F15 | 各模块测试策略（分块/parent-child/解析 golden/状态机/RRF/快照/prompt/拒答/E2E） | MEDIUM | 按 P1/P2 门禁落地 |
| F-02/F-04/F-05/F-11/F-12/F-14/F-17 | Key 慢哈希/限流分层/审计流/path 越权/提示注入/静态加密/模型校验 | MEDIUM | 按 P3/P6 门禁落地 |
| AUD-03/AUD-04/AUD-08/AUD-09/AUD-10/AUD-13/AUD-14 | 版本口径/检索参数/默认端点/路径派生/dev 工具链/.gitignore/安装指引 | LOW-MED | P0 配置整改 |
| ARC-013~019 及 REL-* 系列 | 迁移路径/阈值校准/降级矩阵/热路径缓存/发布制度等 | LOW-MED | 按阶段门禁 |

## 6. 制度文件索引

- 本文件：质量门 + 阶段门禁 + 审计机制 + 缺陷制度
- `docs/audit/`：每轮审计记录（五查 + 偏离处置）
- 分支/提交/评审/发布制度：见 §2 各阶段门禁与 REL-GIT/REVIEW/SHIP/DEFECT（受保护 main、Conventional Commits、四段式 PR 模板、SemVer + Changelog、回滚演练）
