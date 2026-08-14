"""状态机表驱动测试（审计 F4：合法转移逐一验证 + 非法转移矩阵全部拒绝）。"""
import pytest

from core.ingest.state import (
    IllegalTransitionError,
    Stage,
    assert_transition,
    can_transition,
    retry_from,
)

# (from, to, legal) 全矩阵：合法转移白名单之外的任何组合都必须被拒绝
ALL_STAGES = list(Stage)
LEGAL_PAIRS = {
    (Stage.UPLOADED, Stage.PARSED),
    (Stage.UPLOADED, Stage.FAILED),
    (Stage.PARSED, Stage.CHUNKED),
    (Stage.PARSED, Stage.FAILED),
    (Stage.CHUNKED, Stage.EMBEDDED),
    (Stage.CHUNKED, Stage.FAILED),
    (Stage.EMBEDDED, Stage.INDEXED),
    (Stage.EMBEDDED, Stage.FAILED),
    (Stage.INDEXED, Stage.READY),
    (Stage.INDEXED, Stage.FAILED),
    # 契约 v1.1：FAILED 恢复转移
    (Stage.FAILED, Stage.UPLOADED),
    (Stage.FAILED, Stage.PARSED),
    (Stage.FAILED, Stage.CHUNKED),
    (Stage.FAILED, Stage.EMBEDDED),
    (Stage.FAILED, Stage.INDEXED),
}
MATRIX = [
    (frm, to, (frm, to) in LEGAL_PAIRS)
    for frm in ALL_STAGES
    for to in ALL_STAGES
]


@pytest.mark.parametrize("frm,to,legal", MATRIX)
def test_transition_matrix(frm, to, legal):
    assert can_transition(frm, to) is legal
    if legal:
        assert_transition(frm, to)  # 合法转移不抛
    else:
        with pytest.raises(IllegalTransitionError):
            assert_transition(frm, to)


def test_ready_is_terminal():
    assert not can_transition(Stage.READY, Stage.INDEXED)
    assert not can_transition(Stage.READY, Stage.FAILED)


def test_failed_resume_transitions_allowed():
    # 契约 v1.1：FAILED 恢复转移（重新入队从失败阶段重跑）
    for resume in (Stage.UPLOADED, Stage.PARSED, Stage.CHUNKED, Stage.EMBEDDED, Stage.INDEXED):
        assert can_transition(Stage.FAILED, resume)


def test_retry_from_mapping():
    # 契约 §2.3：failed 后从失败阶段重跑（embed 失败从 chunked 重跑，不重算嵌入之前阶段）
    assert retry_from(Stage.PARSED) == Stage.PARSED
    assert retry_from(Stage.CHUNKED) == Stage.PARSED
    assert retry_from(Stage.EMBEDDED) == Stage.CHUNKED
    assert retry_from(Stage.INDEXED) == Stage.EMBEDDED
