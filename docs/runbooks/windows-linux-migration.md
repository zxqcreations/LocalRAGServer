# Windows → Linux 迁移验收清单（Phase 6 门⑦）

> 状态：兼容性清单定稿（2026-08-16）。**实测验收需 Linux 环境（环境受限项，
> v1.0.0 发布如实标注）**；清单依据为 CI 双平台（ubuntu/windows）全绿 +
> 代码审计的跨平台构造。

## 已核验的跨平台构造（CI 双平台每日验证）

| 维度 | 现状 | 依据 |
|---|---|---|
| 路径处理 | 全库 pathlib，无硬编码分隔符 | CI ubuntu 全绿 |
| 日志编码 | configure_logging UTF-8 通道（_utf8_stream 对 Linux 无害 no-op） | 第 14 轮修复 |
| 控制台编码 | smoke/drill 的 reconfigure 用 getattr 保护（Linux 无 reconfigure 时跳过） | CI ubuntu |
| 文件锁语义 | Qdrant local 模式锁差异已用子进程隔离规避（drill 脚本） | 第 25 轮 |
| 测试/门禁 | pytest/ruff/pyright/bandit 双平台 CI 全绿 | ci.yml matrix |
| SQLite/PG | NullPool 分支 + alembic 演练（pg_drill） | 第 2 轮 |

## 迁移切换清单（部署侧，随 Linux 环境执行）

1. **二进制**：llama-server/llama-cli（Linux CUDA 构建）、qdrant（Linux 版）、
   worker 池形态（--pool=solo → prefork，可选）
2. **Python 依赖**：torch cu126 → Linux CUDA wheel（同 pyproject 策略，
   pip 直管不进 lock）
3. **路径配置**：RAG_DATA_DIR 绝对路径 + 权限 700（phase6-plan 附录 A 第 8 项）
4. **代理/网络**：Linux 环境按需配置；UrlFetcher trust_env=False 语义不变
5. **本机专用脚本标注**：scripts/eval_parsing.py 硬编码 D:/ 数据源路径
   （评测脚本，迁移时改数据源配置——已在文件头注明）

## 验收口径（quality.md 门⑦：同模型同 1000 文档对比）

1. 同模型（GGUF 同文件）+ 同 1000 文档导入
2. 检索回归：recall@10/MRR@10 与基线对比（run_retrieval --check-baseline）
3. SLO：search-bench P95 <500ms
4. 全部通过 → 门⑦ 实测闭环（记录验收报告）
