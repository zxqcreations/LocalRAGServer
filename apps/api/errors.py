"""统一错误码目录（docs/quality.md P0-6，审计 ARC-010/F-19）。

调用方可按 code 编程化处理错误；error 字段不泄露堆栈/内部路径。
"""
from typing import NoReturn

from fastapi import HTTPException

# 错误码目录（Phase 0 初版，随阶段扩展：审计 ARC-010 要求 4xxx/5xxx 语义化）
AUTH_REQUIRED = "auth_required"  # 401 缺少 Bearer Key
AUTH_INVALID = "auth_invalid"  # 401 Key 无效
AUTH_UNCONFIGURED = "auth_unconfigured"  # 503 未配置 Key（fail-closed）
KB_NOT_FOUND = "kb_not_found"  # 404
DOC_NOT_FOUND = "doc_not_found"  # 404
UNSUPPORTED_FORMAT = "unsupported_format"  # 415 扩展名不支持
INVALID_FILE_CONTENT = "invalid_file_content"  # 415 魔数与扩展名不符
PAYLOAD_TOO_LARGE = "payload_too_large"  # 413
SSRF_BLOCKED = "ssrf_blocked"  # 403 目标被 SSRF 防护拦截
FETCH_FAILED = "fetch_failed"  # 502 抓取失败（网络/超时/超限）
ACL_DENIED = "acl_denied"  # 403 KB 越权（审计 F-13：显式拒绝，不掩盖）
RATE_LIMITED = "rate_limited"  # 429 限流
EMPTY_DOCUMENT = "empty_document"  # 422 解析后无文本
TOO_MANY_PAGES = "too_many_pages"  # 422 页数超限
VALIDATION_ERROR = "validation_error"  # 422 请求参数校验失败
INTERNAL_ERROR = "internal_error"  # 500
HTTP_ERROR = "http_error"  # 兜底


def raise_http(status_code: int, code: str, message: str) -> NoReturn:
    """抛出带语义错误码的 HTTPException（由全局异常处理器转为统一信封）。"""
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})
