# ADR-003 · TEI 嵌入部署方案（A+D 组合）

- 状态：已批准（用户确认）
- 日期：2026-08-14
- 决策依据：docs/design/tei-deployment-options.md（四案分析 + 实测支撑）

## 决策

- **Phase 2-5**：嵌入用本机 torch + sentence-transformers（bge-m3 dense，Spike 实测 141 条/s、驻留 888MB）；
  混合检索的 sparse 侧用 **Qdrant BM25 文本索引**（本机实测 SparseIndexParams/RRF 全部可用，
  不依赖 bge-m3 学习稀疏——本机 sentence-transformers 无该输出 API，已实测确认）
- **Phase 6（Linux 迁移）**：生产嵌入切 TEI（`turing-` 标签镜像），启用 bge-m3 学习稀疏作为增强项

## 约束

1. 任何嵌入栈切换（torch 版 ↔ TEI 版）必须过评测集回归 + 基线门禁（quality.md T1）
2. 本机 GPU torch 被 uv sync 覆写为 cpu 版的环境风险由 tests/test_gpu_env.py 守卫
3. 千万级 chunk 全量重嵌入的重成本操作须按迁移计划（qdrant-hybrid-migration.md）分批执行

## 替代方案（否决记录）

- B（WSL2+Docker）：需系统级安装授权，且 Turing 上 WSL2 GPU 直通配置复杂——暂缓
- C（远程 TEI）：无可用 Linux 服务器
