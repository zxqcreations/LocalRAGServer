# ADR-006 · MCP transport 范围明确化（v1.0 决策）

> 状态：接受（2026-08-16）
> 背景：安全审计 M-1——架构文档声明 MCP 双 transport（stdio + streamable HTTP），
> 但 streamable HTTP 从未挂载（FastAPI 无 /mcp 挂载点）；声明与实现不符，
> 且若未来挂载时遗忘 ACL 注入即成无防护远程面。

## 决策

**v1.0.0 明确为 stdio-only**：MCP 工具经 stdio transport 提供（scripts/mcp_stdio.py，
主 Key 全权限语义，仅本机进程间调用）。

**streamable HTTP transport 为 v1.1 功能项**，启用前置条件：
1. 挂载点必须注入请求级 ACL resolver（`get_allowed_kbs`，与 REST 同语义）
2. `allow_local_paths=False` 强制
3. TLS 前置（phase6-plan 附录 A 第 6 项：与 REST 同一 TLS 面）
4. 挂载处回归测试（越权矩阵）

## 后果

- 正面：声明与实现一致，无隐性攻击面；stdio 语义简单可靠（本机进程信任边界）
- 负面：Agent 经网络接入 MCP 的能力后置（v1.1）；需文档同步修正双通道声明
- 文档修正：architecture.md §9.2 与 mcp 模块注释标注 stdio-only（v1.0）+ v1.1 路线
