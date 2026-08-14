"""摄取状态机（契约：docs/design/ingest-state-machine.md，质量门禁实现必须逐字遵循）。"""
from enum import StrEnum


class Stage(StrEnum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    CHUNKED = "chunked"
    EMBEDDED = "embedded"
    INDEXED = "indexed"
    READY = "ready"
    FAILED = "failed"


# 合法转移白名单（契约 §2.1）：只能前向推进；failed 为可恢复失败态
# （契约 v1.1：FAILED 的恢复转移——重新入队时从失败阶段重跑，禁止跳级回滚到更早阶段）
_TRANSITIONS: dict[Stage, set[Stage]] = {
    Stage.UPLOADED: {Stage.PARSED, Stage.FAILED},
    Stage.PARSED: {Stage.CHUNKED, Stage.FAILED},
    Stage.CHUNKED: {Stage.EMBEDDED, Stage.FAILED},
    Stage.EMBEDDED: {Stage.INDEXED, Stage.FAILED},
    Stage.INDEXED: {Stage.READY, Stage.FAILED},
    Stage.READY: set(),
    Stage.FAILED: {
        Stage.UPLOADED,
        Stage.PARSED,
        Stage.CHUNKED,
        Stage.EMBEDDED,
        Stage.INDEXED,
    },
}

# failed 恢复映射：重新入队时从失败阶段重跑（契约 §2.3，非从头）
_RETRY_FROM: dict[Stage, Stage] = {
    Stage.UPLOADED: Stage.UPLOADED,
    Stage.PARSED: Stage.PARSED,
    Stage.CHUNKED: Stage.PARSED,
    Stage.EMBEDDED: Stage.CHUNKED,
    Stage.INDEXED: Stage.EMBEDDED,
    Stage.FAILED: Stage.UPLOADED,  # 未知失败位置时从解析重跑（保守）
}


class IllegalTransitionError(ValueError):
    """非法状态转移（契约 §2.1：必须被拒绝，禁止静默接受）。"""


def can_transition(frm: Stage, to: Stage) -> bool:
    return to in _TRANSITIONS[frm]


def assert_transition(frm: Stage, to: Stage) -> None:
    if not can_transition(frm, to):
        raise IllegalTransitionError(f"非法状态转移：{frm.value} → {to.value}")


def retry_from(stage: Stage) -> Stage:
    """failed 后重跑起点（契约 §2.3）。"""
    return _RETRY_FROM[stage]


_STAGE_ORDER = [
    Stage.UPLOADED,
    Stage.PARSED,
    Stage.CHUNKED,
    Stage.EMBEDDED,
    Stage.INDEXED,
    Stage.READY,
]


def stage_reached(current: Stage, target: Stage) -> bool:
    """current 是否已达到/越过 target（幂等判断，契约 §2.2）。"""
    if current == Stage.READY:
        return True
    if current == Stage.FAILED:
        return False
    return _STAGE_ORDER.index(current) >= _STAGE_ORDER.index(target)
