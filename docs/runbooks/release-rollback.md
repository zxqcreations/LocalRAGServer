# 发布与回滚 Runbook（Phase 6 门⑤）

> 状态：定稿（2026-08-16）。覆盖发布流程、回滚三路径、旧模型保留契约、
> per-KB feature flag 评估结论。

## 发布流程

1. **门禁先行**：CI 全绿 + 质量门禁表全项（quality.md §1，含 RAGAS 基线）
2. **打 tag**：`git tag -a vX.Y.Z` + 推送（semver 语义随 quality.md 阶段映射）
3. **部署**（单机 Windows）：
   - `git pull`（或下载 release 包）→ `uv sync`（依赖对齐）→ 停 API/worker
   - 迁移检查：alembic upgrade head（PG 路径）或启动自愈（SQLite 路径）
   - 重启 API + worker；`scripts/smoke.py` 全链路验证
4. **观察期 24h**：metrics P95 劣化 >30% 即回滚（v1.0.0 DoD 契约）

## 回滚三路径（按影响面升序）

| 路径 | 适用 | 步骤 |
|---|---|---|
| 代码回滚 | 无 schema/数据变更的缺陷 | `git checkout <上一 tag>` → 重启 → smoke 验证 |
| 数据回滚 | 摄取/索引数据损坏 | backup-restore.md 场景 A 全量恢复（最新完好快照） |
| 模型回滚 | 嵌入/生成模型退化 | 切换 models/MANIFEST.json 指向的上一版本模型文件 → 重启推理服务 → RAGAS/检索回归验证 |

## 旧模型保留 ≥72h 契约

- 模型文件版本化：更新 MANIFEST.json 时旧文件**不删除**，保留 ≥72h 观察期后清理
- 回滚操作：改 MANIFEST 条目回指旧文件 → 重启（无需重新下载）
- 清理脚本：`scripts/spike/prune_old_models.py`（列出 >72h 未引用的模型文件，
  --apply 执行删除——Phase 6 后续实现，当前以 MANIFEST 手工管理）

## per-KB feature flag 评估（决策记录）

- 目标：新策略（chunk/重排/阈值变更）先在小范围 KB 灰度再全量
- 现有机制：pipeline_version 戳（ARC-003，chunk 时记录，变更判定重索引）——
  支持"按 KB 重索引"，但不支持"同时并存两策略"
- **v1.0 决策**：不做运行时 feature flag（单机部署无多租户灰度场景）；
  灰度以"新建 KB 试新策略 + pipeline_version 戳驱动按 KB 重索引"实现，
  回滚 = 改回策略 + 该 KB 重索引（旧策略数据经 pipeline_version 判定自然重建）
- 记录为架构 v2.0 正式版的"不实现"决策（ADR-006 候选，v1.0.0 前补记）

## 演练记录

- 代码回滚演练：git checkout 上一 tag → smoke 全绿（Phase 6 内执行）
- 数据回滚演练：backup-restore.md 场景 A（已实测，第 25 轮）
- 模型回滚演练：MANIFEST 切换 + 检索回归（GPU nightly，环境受限项）
