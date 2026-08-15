# URL 增量爬取设计（Phase 5 第 2 项）

> 状态：设计定稿。目标：已订阅 URL 的周期性重抓——内容变化自动重索引，
> 未变化仅刷新时间戳（最小成本）。

## 现状与差距

- 已有：单次 URL 摄取（`UrlFetcher` SSRF 5 层防护 + `/kb/{id}/documents/url` 端点，
  内容哈希幂等键 `(kb_id, content_hash)`）
- 差距：无订阅/周期机制——网页内容更新后知识库无感知

## 数据模型（registry 新增 UrlSubscription）

| 字段 | 说明 |
|---|---|
| id | uuid |
| kb_id | 所属知识库（FK 语义） |
| url | 订阅地址（唯一键 kb_id+url） |
| allowlist_domain | 该订阅的域名白名单（UrlFetcher 复用；空 = 任意公网） |
| last_content_hash | 上次摄取的内容哈希（变更检测） |
| last_fetched_at / next_fetch_at | 调度游标 |
| interval_hours | 抓取周期（默认 24） |
| enabled | 暂停开关（不删除订阅保留历史） |
| last_error | 最近一次失败信息（观测） |

## 调度（Celery beat，filesystem broker 兼容）

- **beat 周期任务** `crawl.due`（每 10 分钟）：扫描 `next_fetch_at <= now` 且
  enabled 的订阅，按 last_fetched_at 串行入队 `crawl.fetch(subscription_id)`
- **fetch 任务**：UrlFetcher 抓取 → 内容哈希对比：
  - 未变化：更新 last_fetched_at/next_fetch_at，结束
  - 变化：走既有 URL 摄取链路（新 content_hash → 新 Document 记录；
    旧版保留，管理端按 source 回溯版本）
  - 失败：last_error 记录 + next_fetch_at 退避（×2，上限 interval_hours）
- **版本语义 v1**：新版本 = 新 Document（幂等键自然支持）；检索命中新旧并存
  （索引旧版保留），后续版本可用 superseded 标记优化（v2 项）

## 安全（复用既有防护）

- UrlFetcher 全链路（协议白名单/DNS 全 IP 校验/逐跳重校验/大小上限）
- 订阅仅管理端可创建（RBAC admin）；API Key 通道不暴露订阅管理
- SSRF 面不变：任意订阅的 URL 同样过 5 层防护

## 实施步骤

1. UrlSubscription 表 + registry 方法（create/list/get/update_cursor/mark_error，
   TDD 先行）
2. crawl.due/fetch 任务 + beat schedule（eager 模式可测）
3. 管理端订阅 API + 前端面板（Phase 5 收尾项，可后置）
4. 实测：本地假站点变更 → 重抓 → 新版本可检索
