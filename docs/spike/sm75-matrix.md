# SM75（Turing）硬件兼容矩阵 · Spike 实测报告

> 日期：2026-08-14 · 实机：NVIDIA GeForce RTX 2080 Ti（11GB，Turing SM75）· Windows 11 · 无 Docker
> 依据：docs/quality.md P0-2（审计 ARC-001/ARC-004）

## 1. 实测结论速览

| 路径 | 状态 | 关键数据 |
|---|---|---|
| **llama.cpp CUDA 12.4（生成）** | ✅ 可用（MVP 首选） | Qwen3-8B Q4_K_M：**Prompt 74.0 t/s · 生成 29.7 t/s**，显存 ~1.8GB |
| **torch cu126 + bge-m3（嵌入，GPU）** | ✅ 可用（开发态） | **141 条/s**（批量 16），模型驻留 **≈888MB** |
| torch + bge-m3（CPU 兜底） | ⚠️ 可用但慢 | 5 条/s（GPU 的 1/28），仅应急 |
| vLLM（生成） | ❌ 原生 Windows 不可行 | 官方仅支持 Linux/WSL2；且 ≥0.24 移除 SM75 FlashInfer（审计 ARC-001），须钉旧版 |
| TEI（生产嵌入，turing- 镜像） | ⏸️ 本机无法验证 | 需 Docker；官方支持 SM75（cc 7.5 为最低要求，需 turing- 标签镜像） |
| reranker（bge-reranker-v2-m3） | ⏸️ 未实测 | 同上需 TEI/Docker；CPU 兜底可行（仅影响延迟） |

## 2. 生成栈实测

- 构建：llama.cpp **b10427**（commit 650913862，2026-08，CUDA 12.4 win-x64，**钉版本**）
- 模型：Qwen3-8B-Q4_K_M（unsloth，4bit 量化）
- 参数：`-ngl 40 -n 256 -st`（单轮，全部层 GPU offload）
- 结果：`[ Prompt: 74.0 t/s | Generation: 29.7 t/s ]`；Qwen3 思考模式正常输出中文长链思考 + 回答
- 显存：加载后 GPU 占用 ~1.8GB（余量充足，可同驻 bge-m3 ≈0.9GB，总 ~2.7GB/11GB）
- 结论：**解码 30 tok/s 满足 Agent 场景**（审计预警的 vLLM 0.24 回归 ~1 tok/s 被规避）；吞吐估算需按此实数修订（架构 v1.1 §8.4 的 40~80 tok/s 过于乐观）

## 3. 嵌入栈实测

- torch 2.9.1+cu126（CUDA 12.6 运行时）在 Turing 上正常：`cuda.is_available()=True`
- bge-m3（fp16）：GPU 驻留 ≈888MB（低于架构 Profile D 预估的 2.5GB——TEI 服务口径更重）
- 批量嵌入吞吐：**141 条/s**（320 条 × ~200 字符，batch 16）；CPU 同批 5 条/s
- 结论：千万级 chunk 全量嵌入 ≈ 20 小时（GPU 本机）——印证「生产需 TEI + 更强卡」的架构判断；开发态本机 GPU 可用

## 4. 环境约束与已知问题

| 问题 | 影响 | 处置 |
|---|---|---|
| **uv 0.11.13 不读 `[[tool.uv.index]]`/`[tool.uv.sources]`**（实测：`--index` 把索引名当路径） | GPU torch 无法通过 lock 声明 | 工作区方案：`uv pip install "torch==2.9.1" --index https://download.pytorch.org/whl/cu126`；Phase 1 深查（可能为 uv bug，升级 uv 验证） |
| Windows PyPI 默认 torch 为 +cpu | 常规 `uv sync --extra embed` 得到 CPU 版 | 同上工作区方案；README 已注明 |
| llama-cli 交互模式在 stdin EOF 下死循环刷提示符 | 后台跑挂风险 | 必须用 `-st/--single-turn` 或 llama-server HTTP |
| huggingface_hub 需环境代理（不经注册表） | 权重下载 | 运行时 export HTTP(S)_PROXY=127.0.0.1:7897（README 已注明） |
| 后台长任务在会话轮转时可能被终止 | 长实测中断 | 实测脚本拆小、落盘日志 |

## 5. 对架构文档的影响（v1.2 待办）

1. §8.4 吞吐估算改为实测口径：生成 ~30 tok/s（llama.cpp 实数），嵌入 141 条/s（本机 torch）/ TEI 口径待 Linux 验证
2. §4 Profile D 验证成立：生成（1.8GB）+ 嵌入（0.9GB）≈2.7GB 远低于 11GB，**reranker 可 GPU 常驻**（留待 TEI 验证）
3. §13 部署表补充：Windows 原生用 llama-server（OpenAI 兼容端点，端口 9001 对齐）；Linux 生产用 vLLM 钉版
