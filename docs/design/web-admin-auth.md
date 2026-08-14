# Web 管理端认证设计（Phase 4 进入标准 · 审计 F-03 定稿）

> 状态：已批准 · 2026-08-15
> 背景：Phase 4 Web 管理端为系统最高权限面（KB 管理/文档批量操作/API Key 签发吊销/监控）。
> 管理通道与 API Key 通道**彻底隔离**：REST/MCP 用 Bearer Key，管理端用会话认证。

## 1. 认证与会话

- **初始密码**：首次启动生成一次性随机密码（打印到服务端日志 + 写 data_dir/admin_initial_password，
  首次登录后删除）；首次登录**强制修改**
- **凭据存储**：admin 用户密码用 **Argon2id**（审计 M-6：Web 口令是低熵人类口令，
  与高熵 API Key 的 scrypt 分档；hashlib 无 Argon2 → 引入 `argon2-cffi` 依赖）
- **会话**：登录成功签发会话 Cookie——`HttpOnly; Secure; SameSite=Lax` + 服务端会话表
  （session_id 哈希 + expires_at + 轮换）；登出即吊销
- **CSRF**：会话 Cookie 鉴权 → 所有状态变更请求校验 CSRF token（SameSite=Lax 为第一层，
  token 为第二层）
- **登录限流**：per-IP + per-账号（复用 ADR-005 RateLimiter）

## 2. RBAC（两档，架构 §12/审计 F-03）

| 角色 | 权限 |
|---|---|
| admin | 全部：KB 管理、文档批量操作、API Key 签发/吊销、标注管理、系统配置查看 |
| readonly | 只读：KB/文档/任务/审计列表、指标看板；无任何变更操作 |

## 3. 安全基线

- 管理端**默认绑定 127.0.0.1**（与 API 同规则；非回环启动打印安全警告，审计 F-01 机制复用）
- 管理 API 前缀 `/admin/api/*`，独立于 `/api/v1` 与 `/v1`；中间件层显式拒绝 API Key
  访问管理路由（两通道彻底隔离）
- 审计：登录/登出/改密/Key 签发/Key 吊销全部记入 audit_logs（actor=admin 用户名）
- 会话超时：闲置 30 分钟失效；TLS 在 Phase 6 对外暴露时强制

## 4. 测试契约（Phase 4 门禁）

- 初始密码仅首次显示、强制改密流程、Cookie 属性断言、CSRF 拒绝、RBAC 越权矩阵
  （readonly 执行变更操作 → 403）、管理路由拒绝 API Key、登录限流 429
