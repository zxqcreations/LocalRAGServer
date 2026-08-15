# 备份与恢复 Runbook（Phase 6 门②）

> 状态：定稿 + 脚本化演练（scripts/drill_backup_restore.py）。
> 数据目录布局：`data_dir/`（SQLite 注册表 + Qdrant 本地模式 + ingest_work 源文件）。

## 备份（每 24h + 每次大批量导入前）

```bash
# 停写窗口（可选）：worker 暂停入队
tar -czf backup-$(date +%Y%m%d-%H%M).tar.gz data/   # 或 robocopy /MIR 到备份盘（Windows）
```

要点：
- SQLite 与 Qdrant 必须**同一次快照**（跨库一致性：注册表文档与向量点对应）
- 备份校验：解包后 `sqlite3 data/registry.db "PRAGMA integrity_check;"` → ok
- 备份存储：异机/加密盘；保留 ≥30 天

## 场景 A · 全量丢失恢复（SQLite + Qdrant + 源文件）

1. 停 API/worker
2. 解包备份覆盖 `data/`
3. 启动 API → `/readyz` database/qdrant 双 ok
4. 校验：文档数/chunk 数与灾前一致（`drill` 脚本 `--verify-only` 可自动比对）

## 场景 B · 向量库损坏（SQLite 完好，Qdrant 丢失/损坏）

1. 停 API
2. 删除 `data_dir/qdrant/`（或从备份恢复 SQLite 后 Qdrant 缺失）
3. 启动 API → readyz `qdrant: down`（检测生效）
4. **重索引路径**：
   - 从备份恢复 `ingest_work/`（源文件）与 SQLite
   - 运行 `scripts/drill_backup_restore.py --rebuild`：
     遍历注册表 READY/INDEXED 文档 → 源文件存在则重跑 embed+index；
     源文件缺失的文档标记 `reindex_missing_source`（审计记录，人工处置）
5. 对账：`rebuild` 完成后逐 KB 校验 chunk 数与向量点计数一致

## 演练记录

`uv run python scripts/drill_backup_restore.py`（场景 A + 场景 B 检测自动演练，
报告写入 docs/perf/backup-drill-<date>.md）。
