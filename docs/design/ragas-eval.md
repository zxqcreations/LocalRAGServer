# RAGAS 评估闭环设计（Phase 5）

> 状态：设计定稿。目标：检索+生成质量的可量化闭环——每次策略变更
> （chunk/重排/阈值/提示词）跑同一评测集，指标劣化即阻断。

## 基础设施

| 组件 | 现状 | 用途 |
|---|---|---|
| llama-server.exe（llama.cpp） | models/llamacpp/ 已就绪 | OpenAI 兼容 LLM 端点（:9001），RAGAS 评判 + 答案生成 |
| Qwen3-8B Q4_K_M GGUF | models/gguf/ 已就绪 | 上述服务模型（实测 29.7 t/s） |
| bge-m3（sentence-transformers） | 本机已装 | RAGAS 上下文嵌入（ContextPrecision/Recall 需要） |
| eval/datasets/qa.jsonl | 50 条（question + **reference_answer** + anchors） | 评测集 v1（reference 答案已具备，RAGAS 输入完备） |

## 依赖策略（与 torch 一致）

- ragas/langchain 系列**不进 uv.lock**：依赖树重且仅评测路径需要；
  CI 评测 job 不跑 RAGAS（LLM 评判需 GPU/本地服务）。
- 安装（实测验证，2026-08-15）：
  ```bash
  # 代理对 pypi.org 转发故障时走国内镜像（直连可达）：
  uv pip install ragas langchain-openai \
    --index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/
  # ragas 0.4.3 硬导入 langchain_community.chat_models.vertexai，
  # 而 langchain-community 0.4.x 已移除该模块 → 必须降级：
  uv pip install "langchain-community<0.4" \
    --index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/
  ```
  验证：`uv run python -c "import ragas, torch; print(torch.cuda.is_available())"`
  （ragas 导入成功且 torch GPU 完好——安装不破坏既有 GPU 栈）。

## 评测流程（eval/ragas_runner.py）

1. 加载评测集（load_qa，扩展 reference 字段，validate_dataset 同步扩展）
2. 每条 QA：
   - 检索：SearchService（真实嵌入/重排配置，与生产同路径）
   - 生成：ChatClient → llama-server（:9001）
3. RAGAS 四指标（langchain 评测链，LLM 评判指向本地 llama-server）：
   - **faithfulness**（生成忠于上下文）
   - **answer_relevancy**（答案切题）
   - **context_precision**（检索命中上下文的相关性排序）
   - **context_recall**（正确答案信息被检索覆盖的程度）
4. 输出：逐条明细（JSONL）+ 汇总报告（docs/perf/ragas-<date>.md）
5. 门禁：`--check-baseline` 对照基线（docs/perf/ragas-baseline.json，
   容差 0.05，低于基线即非零退出——与 run_retrieval 同机制）

## 可测性契约

- `run_eval(dataset, search_service, chat_client, judge_llm, judge_embeddings) -> list[EvalRecord]`
  纯函数形态：检索/生成/评判全部注入——测试注入 stub 即可离线验证
  流程与报告格式（LLM 评判不发真实请求）。
- 启动 llama-server 为运维脚本（scripts/serve_llamacpp.py 或文档化命令），
  不进评测核心。

## 实施步骤

1. ragas_runner.py 骨架 + 依赖检查（TDD：stub 注入离线测试）
2. 本机实测：llama-server 启动 → 全量评测 → baseline.json 落盘
3. 接入质量门禁表（docs/quality.md §1 增补 RAGAS 基线项）
