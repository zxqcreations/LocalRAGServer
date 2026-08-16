# TEI 部署方案选项分析（Phase 2 进入标准）

> 状态：**已决策**（2026-08-14，用户选择 A+D 组合，见 ADR-003）
> A = 本机 torch 嵌入（bge-m3，实测 141 chunks/s）；D = TEI 预留（Linux 部署时切换）。
> 背景：架构 §3 生产嵌入走 TEI（text-embeddings-inference）；实机无 Docker、无 WSL2，
> 本机开发态已有 torch + sentence-transformers（Spike 实测 bge-m3 dense 141 条/s、驻留 888MB）。

## 方案对比

| 方案 | 优点 | 缺点 | 前置条件 |
|---|---|---|---|
| **A. 本机 torch 嵌入（维持现状）** | 已实测可用；零新基础设施；sparse 输出待验证即可用 | 与生产形态不一致（生产是 TEI 镜像）；长驻 GPU 内存与生成模型共存需按 Profile D 调度 | 验证 sentence-transformers 的 bge-m3 sparse 输出 |
| B. 安装 WSL2 + Docker → TEI turing- 镜像 | 与生产一致（架构 §13）；TEI 官方支持 SM75 | 系统级安装需授权；GPU 直通 WSL2 在 Turing 上需 nvidia 驱动配置；引入第二套运行环境 | 用户授权 WSL2 安装 |
| C. 远程 TEI（另一台 Linux 机器） | 生产形态；本机无负担 | 需要第二台机器；网络延迟 | 有可用 Linux 服务器 |
| D. 显式延期 TEI 至 Phase 6 Linux 迁移 | 不阻塞 Phase 2（混合检索用本机 bge-m3 即可实现与评测） | 开发/生产嵌入栈差异存续至 Phase 6 | 审计登记延期理由 |

## 推荐（2026-08-14 更新：方案 A 已验证完整可行）

**A + D 组合**：实测确认 Qdrant BM25 稀疏索引 + RRF 在本机全部可用，
Phase 2 混合检索无需 bge-m3 学习稀疏与 TEI 即可实现（dense bge-m3 + BM25 + RRF）。
TEI 作为 Phase 6 Linux 迁移的增强项落地（生产嵌入吞吐 + bge-m3 学习稀疏）。
差异风险由「评测集 + 基线门禁」约束（任何嵌入栈切换必须过评测回归，quality.md T1）。

## 待用户选择

A/D（推荐）/ B / C——选定后此文档升为 ADR-003。
