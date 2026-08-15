# Qdrant 部署形态 Runbook（SLO 压测结论，门③）

> 状态：定稿（2026-08-16 万级实测）。两种形态边界如下。

## 形态对照（实测数据）

| 形态 | 检索方式 | 万级 P95 | 适用 |
|---|---|---|---|
| local 模式（默认开发） | brute-force 精确 | 749.3ms **超限** | 开发/小数据（<2k chunk） |
| **server 模式（生产）** | HNSW 近似 | **160.1ms 达标** | 生产（目标 <500ms） |

## 启动（Windows 本机，二进制在 models/qdrant-server/qdrant.exe）

```bash
models/qdrant-server/qdrant.exe --uri http://127.0.0.1:6333
# HTTP 6333 · gRPC 6334；数据落于 ./storage（工作目录）
```

应用侧配置：`RAG_QDRANT_URL=http://127.0.0.1:6333`（Settings qdrant_url 非空即
走 server 模式；empty 时回退 qdrant_path local 模式）。

## 生产要求

- 仅回环绑定（Phase 6 门① 附录 A 第 6 项）；TLS 由反向代理统一终止
- 备份：server 模式数据目录（storage/）纳入备份快照（backup-restore.md）
- 万级以上的容量规划：HNSW m=16/ef=128 默认参数下 1 万 chunk 内存占用低；
  百万级需评估 ef 调优（对照架构 §8.4）

## 性能契约（门③ 基线）

- 万级（1 万文档/1 万 chunk）+ chunk 序列缓存：P95 160.1ms（2026-08-16 实测）
- 耗时主成分：sparse（BM25 纯 Python）84.6ms > fuse 116.8ms > dense 36.8ms
- 后续优化候选项：sparse 段倒排检索结构优化（Phase 6 后）
