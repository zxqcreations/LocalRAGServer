"""MCP Server（架构 §9.2）：5 工具，stdio 与 streamable HTTP 双 transport 共用内核。

工具描述嵌入 KB 目录与使用示例，提升 Agent 工具选择准确率（quality.md Phase 3 门）。
ACL：stdio 本机通道为主 Key 语义（"*"，审计 F-11 本地特权通道）；
streamable HTTP 由挂载方（FastAPI 中间件）注入请求级 ACL。
审计：每次成功工具调用落 record_audit（mcp_* 独立动作码，与 REST 同库同口径）。
"""
import shutil
from collections.abc import Callable
from pathlib import Path

import mcp.types as types
import structlog
from mcp.server import Server

from core.config import Settings
from core.ingest.importer import _content_hash
from core.observability.logging import get_logger
from core.retrieval.search import SearchService
from core.security.acl import AllowedKbs, require_kb_access
from core.storage.registry import Registry

_logger = get_logger("local_rag_server.mcp")

AllowedResolver = Callable[[], AllowedKbs]
ActorResolver = Callable[[], str]


def build_mcp_server(
    registry: Registry,
    search_service: SearchService,
    settings: Settings,
    allowed_resolver: AllowedResolver | None = None,
    allow_local_paths: bool = True,
    actor_resolver: ActorResolver | None = None,
) -> Server:
    """构建 MCP Server；allowed_resolver 注入请求级 ACL（默认全权限，stdio 语义）。

    allow_local_paths：本地文件摄取开关（审计 H-2）——stdio 本机通道为 True；
    streamable HTTP 挂载时必须传 False（远程通道拒绝本地路径，防任意文件读取）。

    actor_resolver：审计主体注入——HTTP 通道为 Key id 或 master，
    stdio 无请求上下文，记 mcp:stdio。
    """
    server = Server("local-rag-server")

    def _allowed() -> AllowedKbs:
        return allowed_resolver() if allowed_resolver else "*"

    def _audit(action: str, kb_id: str) -> None:
        """工具调用审计：成功路径落 mcp_* 动作码（越权拒绝与 REST 403 同口径不落审计）。

        trace_id 取自 structlog contextvars——HTTP 通道由 trace_middleware
        （已覆盖 /mcp）绑定，stdio 通道为空串。
        """
        actor = actor_resolver() if actor_resolver else "mcp:stdio"
        trace_id = structlog.contextvars.get_contextvars().get("trace_id", "")
        registry.record_audit(actor=actor, action=action, kb_id=kb_id, trace_id=trace_id)

    def _kb_names() -> str:
        kbs = registry.list_kbs()
        allowed = _allowed()
        if allowed != "*":
            kbs = [k for k in kbs if k.id in allowed]
        return "、".join(f"{k.name}({k.id[:8]}…,{k.kb_type})" for k in kbs) or "（暂无知识库）"

    def _resolve_kb(name_or_id: str):
        kb = registry.get_kb(name_or_id)
        if kb is None:
            kb = next((k for k in registry.list_kbs() if k.name == name_or_id), None)
        if kb is None:
            raise ValueError(f"知识库不存在：{name_or_id}")
        require_kb_access(kb.id, _allowed())
        return kb

    def _tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="search_knowledge",
                description=(
                    "在知识库中检索相关内容（混合检索：语义 + 关键词，重排后返回带引用 chunk）。"
                    f"可用知识库：{_kb_names()}。适合：先检索再自行推理的场景。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索查询（自然语言）"},
                        "kb": {"type": "string", "description": "知识库 ID 或名称"},
                        "top_k": {"type": "integer", "description": "返回条数", "default": 5},
                    },
                    "required": ["query", "kb"],
                },
            ),
            types.Tool(
                name="list_knowledge_bases",
                description="列出当前可访问的知识库（名称/ID/类型）。",
                input_schema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="ask",
                description=(
                    "基于知识库内容回答问题（检索 + 生成，返回带 [n] 引用的答案）。"
                    "适合：需要完整答案而非原始 chunk 的场景。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "问题"},
                        "kb": {"type": "string", "description": "知识库 ID 或名称"},
                    },
                    "required": ["question", "kb"],
                },
            ),
            types.Tool(
                name="ingest_document",
                description=(
                    "将本机文件路径提交到摄取队列（异步处理，返回 job_id）。"
                    "仅限本机 stdio 通道使用（审计 F-11：远程通道不接受本地路径）。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "本机文件绝对路径"},
                        "kb": {"type": "string", "description": "知识库 ID 或名称"},
                    },
                    "required": ["path", "kb"],
                },
            ),
            types.Tool(
                name="get_document_status",
                description="查询摄取任务或文档的处理状态。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "摄取任务 ID"},
                    },
                    "required": ["job_id"],
                },
            ),
        ]

    async def _handle_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=_tools())

    async def _handle_call_tool(
        ctx, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        try:
            if name == "list_knowledge_bases":
                kbs = registry.list_kbs()
                allowed = _allowed()
                if allowed != "*":
                    kbs = [k for k in kbs if k.id in allowed]
                text = "\n".join(f"- {k.name}（id={k.id}，类型={k.kb_type}）" for k in kbs)
                _audit("mcp_list_kbs", "")
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=text or "（无可访问知识库）")]
                )

            if name == "search_knowledge":
                kb = _resolve_kb(arguments["kb"])
                results = search_service.search(
                    kb.id, arguments["query"], int(arguments.get("top_k", 5))
                )
                _audit("mcp_search", kb.id)
                if not results:
                    return types.CallToolResult(
                        content=[types.TextContent(type="text", text="（无检索结果）")]
                    )
                text = "\n\n".join(
                    f"[{i}] 文档 {r.doc_title}（分数 {r.dense_score:.3f}）：\n{r.content}"
                    for i, r in enumerate(results, start=1)
                )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=text)]
                )

            if name == "ask":
                kb = _resolve_kb(arguments["kb"])
                results = search_service.search(
                    kb.id, arguments["question"], settings.rerank_top_k
                )
                _audit("mcp_ask", kb.id)
                if not results:
                    return types.CallToolResult(
                        content=[types.TextContent(type="text", text="知识库中未找到相关内容。")]
                    )
                from core.generation.llm import build_rag_messages

                messages = build_rag_messages(
                    arguments["question"], [r.expanded_content for r in results]
                )
                answer = _call_llm(messages, settings)
                cited = f"{answer}\n\n引用：\n" + "\n".join(
                    f"[{i}] {r.doc_title}（{r.dense_score:.3f}）"
                    for i, r in enumerate(results, start=1)
                )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=cited)]
                )

            if name == "ingest_document":
                if not allow_local_paths:
                    return types.CallToolResult(
                        content=[
                            types.TextContent(
                                type="text",
                                text="本通道不支持本地文件摄取（审计 H-2：仅 stdio 本机通道可用）",
                            )
                        ],
                        is_error=True,
                    )
                kb = _resolve_kb(arguments["kb"])
                src = Path(arguments["path"])
                if not src.is_file():
                    raise ValueError(f"文件不存在：{src}")
                content_hash = _content_hash(src)
                existing = registry.find_document_by_hash(kb.id, content_hash)
                if existing is not None:
                    return types.CallToolResult(
                        content=[
                            types.TextContent(
                                type="text", text=f"已存在（幂等跳过）：{existing.id}"
                            )
                        ]
                    )
                # 审计 M-3：source 不落绝对路径（仅存 basename 形态）
                doc = registry.create_document(kb.id, src.name, f"local://{src.name}", content_hash)
                job = registry.create_job(doc.id, kb.id)
                work = settings.data_dir / "ingest_work" / job.id
                work.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, work / f"source{src.suffix.lower()}")
                from core.ingest.tasks import enqueue_ingest

                enqueue_ingest(job.id)
                _audit("mcp_ingest", kb.id)  # 审计在实际入队完成后（审查 L：先做事后记账）
                return types.CallToolResult(
                    content=[
                        types.TextContent(type="text", text=f"已入队：job_id={job.id}")
                    ]
                )

            if name == "get_document_status":
                job = registry.get_job(arguments["job_id"])
                if job is None:
                    return types.CallToolResult(
                        content=[
                            types.TextContent(
                                type="text", text=f"任务不存在：{arguments['job_id']}"
                            )
                        ]
                    )
                # 审计 M-7：任务归属 KB 必须落在调用方 ACL
                # （跨库 job_id 探测显式拒绝，与 REST 403 语义一致）
                require_kb_access(job.kb_id, _allowed())
                _audit("mcp_status", job.kb_id)
                text = (
                    f"job={job.id} · 阶段={job.stage} · 重试={job.attempt}"
                    + (f" · 错误={job.error}" if job.error else "")
                )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=text)]
                )

            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"未知工具：{name}")],
                is_error=True,
            )
        except ValueError as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], is_error=True
            )
        except PermissionError as exc:
            # 安全审查 L：越权拒绝落审计（探针证据）；kb 原文截断 64 防超长注入
            if name == "get_document_status":
                job = registry.get_job(arguments.get("job_id", ""))
                kb_id = job.kb_id if job is not None else ""
            else:
                kb_id = str(arguments.get("kb", ""))[:64]
            _audit(f"mcp_{name}_denied", kb_id)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], is_error=True
            )
        except Exception:
            # 安全审查 M：内部错误不回显细节（LLM 端点/文件路径等），只进服务端日志
            _logger.exception("mcp_tool_error", detail=f"tool={name}")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="工具执行失败（详情见服务端日志）")],
                is_error=True,
            )

    server.add_request_handler("tools/list", types.RequestParams, _handle_list_tools)
    server.add_request_handler("tools/call", types.CallToolRequestParams, _handle_call_tool)
    return server


def _call_llm(messages: list[dict], settings: Settings) -> str:
    from core.generation.llm import ChatClient

    client = ChatClient(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout
    )
    try:
        return client.chat(messages).content
    finally:
        client.close()
