# Phase 6 生产就绪计划（quality.md §2 七项关键门）

> 状态：启动评估。目标：v1.0.0 发布（24h 观察期，P95 劣化>30% 即回滚）。
> 单机 Windows 部署约束下逐项可行性与先后排序如下。

## 七项逐项评估

| # | 关键门 | 本机可行性 | 计划 |
|---|---|---|---|
| ① | 安全审计（TLS/RBAC/审计流/密钥轮换/敏感内容策略） | ✅ 代码+文档可审计 | 全库 security-reviewer 深度审计 + 安全策略文档（TLS 部署章节、密钥轮换 runbook、敏感内容策略定稿） |
| ② | 备份+恢复演练（两场景 runbook 实测） | ✅ 本地实测 | 场景 A：SQLite data 目录全量备份恢复；场景 B：Qdrant 快照+SQLite 不一致恢复；runbook 文档 + 脚本化演练 |
| ③ | SLO 压测达标（满数据量 P95<500ms + 耗时分解） | ⚠️ 满数据量（10 万级）单机 25 小时 | 万级合成数据实测 + 线性外推 + 检索耗时分解（dense/BM25/RRF/回查各段） |
| ④ | 故障演练（逐依赖击杀验证降级矩阵） | ✅ 本地实测 | 停 Qdrant/DB/LLM/worker 四场景；readyz 降级判定 + 检索降级行为验证 |
| ⑤ | 发布/回滚 runbook 演练（per-KB feature flag，旧模型保留 ≥72h） | ⚠️ 部分 | runbook 文档 + pipeline_version 回滚演练（现有机制）；feature flag 机制评估（v1.0 前可选） |
| ⑥ | 灰度放量验证 | ❌ 单机受限 | 以 ⑤ 的 feature flag + 双 collection 切换机制替代（架构既有设计）；真实灰度放量随 Linux 部署 |
| ⑦ | Windows→Linux 迁移验收 | ❌ 无 Linux 环境 | 迁移文档 + 兼容性清单（paths/编码/进程形态）；实测随生产 Linux 环境 |

## 实施顺序（依赖驱动）

1. ①安全审计 → ②备份恢复 → ④故障演练（三者互为安全基线）
2. ③SLO 压测（万级合成 + 分解）
3. ⑤发布回滚 runbook
4. ⑥⑦ 文档化 + 环境受限项明示（v1.0.0 发布时如实标注）

## 风险登记

- ③满数据量无法本机实测：以万级外推 + 架构 §8.4 口径为验收依据，发布观察期兜底
- ⑥⑦ 环境受限：v1.0.0 声明中如实标注，不虚报完成

## 附录 A · TLS 部署前置条件清单（门①交付物，安全审计域 1）

1. **反向代理终止 TLS**：nginx/caddy 前置 443；ACME 自动续期；HTTP→HTTPS 301；TLS≥1.2 优先 1.3；私钥 600
2. **代理头透传**：uvicorn `--proxy-headers --forwarded-allow-ips <信任网段>`（否则 per-IP 限流坍缩 + 审计 IP 失真）；nginx `X-Forwarded-For` 设置
3. **会话 Cookie**：启用 `Secure` + `__Host-` 前缀（admin.py:77-89 预留位）
4. **安全头**：HSTS/nosniff/X-Frame-Options DENY/Referrer-Policy/基础 CSP
5. **暴露面收敛**：/docs、/openapi.json、/redoc、/readyz 移出公开或回环限定；`RAG_HOST` 保持 127.0.0.1；`url_fetch_allow_loopback` 保持 false
6. **内部服务隔离**：qdrant(6333)/postgres(5432)/redis(6379)/llm(9001)/judge(9002) 仅回环；MCP HTTP transport 若启用须走同一 TLS 面 + ACL 注入
7. **请求体对齐**：nginx `client_max_body_size` ≥ `RAG_MAX_UPLOAD_MB`；JSON 端点 body 上限（M-5）
8. **数据目录防护**：data_dir 700、初始密码文件 600、备份加密存储
9. **限流升级**：生产切 Redis 令牌桶（ADR-005），多 worker 才有效
