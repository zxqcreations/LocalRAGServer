"""结构化日志（observability.md §2）：structlog JSON 行 + 字段白名单 + 脱敏。"""
import logging
import sys

import structlog

# 日志字段白名单（observability.md 定稿；请求体/查询文本不落日志，审计 F-19）
_ALLOWED_KEYS = {
    "event", "level", "timestamp", "trace_id", "actor", "kb_id",
    "duration_ms", "status_code", "alert", "metric", "detail",
}


def _filter_fields(logger, method_name, event_dict):
    """白名单过滤：未登记字段丢弃（防敏感数据误入日志）。"""
    return {k: v for k, v in event_dict.items() if k in _ALLOWED_KEYS}


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _filter_fields,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str = "local_rag_server"):
    return structlog.get_logger(name)


def emit_alert(name: str, detail: str, level: str = "warning") -> None:
    """告警事件（observability.md §3：alert=true 结构化事件，Phase 6 接 Alertmanager）。"""
    logger = get_logger().bind(alert=True, metric=name, detail=detail)
    getattr(logger, level)("alert", **{"metric": name, "detail": detail})
