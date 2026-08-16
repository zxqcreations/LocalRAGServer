"""结构化日志（observability.md §2）：structlog JSON 行 + 字段白名单 + 脱敏。"""
import logging
import sys

import structlog

# 日志字段白名单（observability.md 定稿；请求体/查询文本不落日志，审计 F-19）
# structlog-integration.md D2 埋点新增：doc_id/stage/hits/model/aborted/limit；
# exception 为 ExceptionRenderer 产物（代码审查 H1：异常诊断信息不能丢）
_ALLOWED_KEYS = {
    "event", "level", "timestamp", "trace_id", "actor", "kb_id",
    "doc_id", "stage", "hits", "model", "aborted", "limit", "duration_ms",
    "status_code", "alert", "metric", "detail", "exception",
}


def _utf8_stream(stream):
    """将文本流就地切换为 UTF-8 输出（errors=replace 兜底），原流返回。

    Windows 控制台常见 cp1252/cp437 代码页无法编码中文日志，写入即抛
    UnicodeEncodeError（CI windows runner 实测）；本项目目标平台为 Windows，
    日志通道必须与代码页解耦。用 reconfigure 而非新建 TextIOWrapper：
    包装会劫持原流（如 pytest 捕获对象）的 buffer 生命周期导致 teardown 崩溃。
    无 reconfigure 的流（StringIO 等）原样返回。
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    return stream


def _filter_fields(logger, method_name, event_dict):
    """白名单过滤：未登记字段丢弃（防敏感数据误入日志）；
    detail 自由字段截断 500 字符（安全审计 L-9：半开放字段的兜底钳制）。"""
    filtered = {k: v for k, v in event_dict.items() if k in _ALLOWED_KEYS}
    if isinstance(filtered.get("detail"), str) and len(filtered["detail"]) > 500:
        filtered["detail"] = filtered["detail"][:500] + "…"
    return filtered


def configure_logging(level: str = "INFO") -> None:
    out = _utf8_stream(sys.stdout)
    # force=True：应用工厂多次调用（测试/多进程）时重新应用同一配置
    logging.basicConfig(
        stream=out, level=getattr(logging, level.upper(), logging.INFO), force=True
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            # 先渲染异常为字符串再过滤：logger.exception 的 exc_info 才有处可去（审查 H1）
            structlog.processors.ExceptionRenderer(),
            _filter_fields,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=out),
    )


def get_logger(name: str = "local_rag_server"):
    return structlog.get_logger(name)


def emit_alert(name: str, detail: str, level: str = "warning") -> None:
    """告警事件（observability.md §3：alert=true 结构化事件，Phase 6 接 Alertmanager）。"""
    logger = get_logger().bind(alert=True, metric=name, detail=detail)
    getattr(logger, level)("alert", **{"metric": name, "detail": detail})
