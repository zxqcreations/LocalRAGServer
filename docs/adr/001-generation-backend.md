# ADR-001 · 生成后端选型（Turing SM75 / 无 Docker 环境）

- 状态：已批准（Spike 实测支撑，docs/spike/sm75-matrix.md）
- 日期：2026-08-14
- 决策背景：审计 ARC-001（vLLM ≥0.24 移除 SM75 FlashInfer 后端，22K 上下文解码吞吐跌至 ~1 tok/s；AWQ 在 SM75 需 legacy 内核）+ 实机无 Docker + 原生 Windows

## 决策

| 场景 | 选型 | 依据 |
|---|---|---|
| **Phase 0-2（本机 MVP/开发）** | **llama.cpp llama-server**（GGUF Q4_K_M，CUDA 12.4 构建） | 实测解码 29.7 t/s；Turing 支持成熟；原生 Windows 可行；自带 OpenAI 兼容端点 |
| 嵌入（开发态） | 本机 torch cu126 + sentence-transformers | 实测 141 条/s、驻留 888MB |
| 生产嵌入 | TEI（`turing-` 标签镜像，Linux） | 官方 SM75 支持；本机无 Docker 暂缓验证（Phase 6 随 Linux 迁移） |
| 生产生成（Linux/WSL2 迁移后） | **vLLM 钉版**（0.21/0.23 + awq_marlin，禁止 latest） | SM75 实测栈经社区验证；升级必须先过评测回归（quality.md T1） |

## 约束（不可妥协）

1. **禁止 latest 镜像/版本漂移**：llama.cpp 钉 b10427；vLLM 钉版写入部署锁定文件；升级走评测回归门禁
2. 吞吐预期一律以实测为准（生成 ~30 tok/s 本机口径），禁止引用未实测数字
3. llama-cli 非交互使用必须 `-st/--single-turn`；服务形态统一走 **llama-server**（OpenAI 兼容，配置 `RAG_LLM_BASE_URL=http://127.0.0.1:9001/v1` 对齐架构 §13）
4. 模型文件纳入 MANIFEST 管理（名称/版本/sha256/来源 URL，审计 F-17）；下载走显式代理

## 已知问题

- uv 0.11.13 忽略 pyproject 索引配置 → GPU torch 用 `uv pip install --index` 工作区方案（Phase 1 验证 uv 升级后是否修复，见 sm75-matrix §4）

## 后果（正面/负面）

- 正面：MVP 即可在实机全链路（GPU 嵌入 + 30 tok/s 生成）；规避 vLLM SM75 回归风险
- 负面：GGUF 量化损失少量质量（Q4_K_M）；llama-server 并发吞吐低于 vLLM（Agent 单路场景可接受）；Phase 6 需一次生成栈切换演练
