# ADR-002 · Worker 部署形态与消息代理（无 Docker/WSL2 环境）

- 状态：已批准
- 日期：2026-08-14
- 决策背景：Phase 1 进入标准③（worker 部署形态决策）；实机无 Docker、无 WSL2、无 Redis、无 PostgreSQL；架构 §13 生产目标为 Celery + Redis + PG

## 决策

| 组件 | 本机（Phase 1-2 开发验证） | 生产（Linux，Phase 6 迁移） |
|---|---|---|
| 任务队列 | **Celery + filesystem 代理**（kombu 内置，零新基础设施；单机够用） | Celery + Redis |
| Worker 形态 | Windows 原生进程（venv + 启动脚本，见 scripts/ 待补） | Docker 容器（架构 §13） |
| PostgreSQL | Windows 官方原生安装（**需用户安装或授权**；Phase 1 迁移演练前提） | Docker PG 16 |
| 兼容性 | broker_url 配置化（`RAG_CELERY_BROKER_URL`），切换只改配置 | — |

## 依据

1. Celery 的价值在分布式 worker 横向扩展（架构 §8.4：解析是真正瓶颈须多 Worker）——filesystem 代理在单机开发期语义足够，生产切 Redis 零代码改动
2. Redis 官方不支持 Windows；非官方移植版已停更，排除
3. WSL2 未安装且安装需系统级操作（需用户授权），不作为当前阻塞项；架构 §13 已保留 WSL2 路径
4. 状态机契约（docs/design/ingest-state-machine.md）的 ingest_jobs 表 + Celery chain 与代理选择解耦——即使未来换代理，任务代码不变

## 约束

1. 任何任务实现不得依赖 broker 特性（如 Redis 优先级），只依赖 Celery 通用语义（重试/chain/revoke）
2. 重试上限 5 次 + DLQ（ingest_jobs.attempt 字段承载），与契约一致
3. PostgreSQL 迁移演练为 Phase 1 退出标准：若用户不安装 PG，演练**显式延期**并在审计记录登记（不静默跳过）

## 已知风险

- filesystem 代理无跨机分发能力（本机多进程 OK）——开发期可接受
- Windows 上 Celery worker 需 `--pool=solo`（prefork 在 Windows 需特殊处理），吞吐受限——Phase 1 验证功能正确性，性能基准以任务耗时记录为准
