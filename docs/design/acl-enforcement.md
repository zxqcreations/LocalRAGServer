# ACL 强制点设计（Phase 3 进入标准 · 审计 F-13 定稿）

> 状态：已批准 · 2026-08-15
> 核心原则：kb_id 的最终过滤值**只由服务端推导**（Key ACL 与请求 kb_id 求交集），
> 绝不接受客户端裸传；越权返回 403（不用 404 或空结果掩盖，避免可探测性歧义）。

## 1. 数据模型

`api_keys` 表（架构 §8.1）：
- id（uuid）、name、key_hash（**scrypt 慢哈希 + 独立盐**，审计 F-02）、
  kb_acl（json：`["kb1","kb2"]` 或 `["*"]`）、expires_at（可空）、created_at、last_used_at
- 明文 Key 仅签发响应返回一次；DB 不存明文（安全测试断言：DB 中无明文 Key，F-18）

## 2. 强制点（单一入口）

```python
# core/security/acl.py
def resolve_allowed_kb_ids(acl: list[str]) -> set[str] | Literal["*"]:
    """解析 Key 的 ACL；'*' 表示全部 KB。"""

def intersect_kb(kb_id: str | None, allowed) -> str | None:
    """请求 kb_id 与 ACL 求交集：未授权 → 403（调用方抛）；未指定 kb_id 且非 '*' → 拒绝。"""
```

- 中间件层：Bearer Key 校验（现有）→ 加载 Key 记录 → 注入 `request.state.key` 与 `request.state.allowed_kbs`
- 路由层：search/chat/ingest/url 等入口统一调用 `require_kb_access(kb_id, request)`；
  未来 GraphRAG/embeddings/rerank 代理同样经此入口（审计 ARC-005：全路径强制）
- 单主 Key（settings.api_key，Phase 0 遗留）过渡语义：等价 `kb_acl=["*"]`，Phase 3 内迁移到
  api_keys 表并保留 settings 主 Key 作为引导凭据（文档化，Phase 4 Web 端接管签发）

## 3. 跨 KB 泄漏测试矩阵（进 CI，永不豁免）

| 用例 | 预期 |
|---|---|
| A Key（仅 kb1）查 kb2 | **403**（非空结果、非 404） |
| A Key（仅 kb1）查 kb1 | 200 |
| 无 Key / 错 Key | 401 |
| 过期 Key | 401 |
| 未指定 kb_id 的检索接口（若存在） | 403（deny by default） |
| 生成链路引用剥离 | citations 不含越权 KB 的 chunk |

## 4. Key 生命周期（审计 F-02）

- 签发：`secrets.token_urlsafe(32)`；响应返回一次明文；DB 存 scrypt 哈希 + 盐
- 吊销：删除记录即时生效（无缓存层——校验直查 DB；性能优化留 Phase 4 Redis 缓存 + TTL ≤60s 吊销传播）
- 轮换：签发新 Key → 并行期 → 吊销旧 Key（API 支持，Web 端引导）
- 过期：expires_at 检查（`now > expires_at → 401`）

## 5. 审计（ARC-005/F-05）

`audit_logs` 表：id、actor（key_id/admin）、action（search/ingest/delete/key_manage）、
kb_id、ip、trace_id、created_at；只追加不修改；Web/REST/MCP 共用同一审计管线。
