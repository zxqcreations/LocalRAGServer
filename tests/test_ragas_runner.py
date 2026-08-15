"""RAGAS 评估闭环测试（docs/design/ragas-eval.md：核心流程离线可测，stub 注入）。"""
import json

import pytest

from eval.dataset import QAEntry
from eval.ragas_runner import (
    TOLERANCE,
    check_baseline,
    check_ragas_deps,
    run_eval,
    summarize,
    write_report,
)

_ENTRIES = [
    QAEntry(
        id="qa-1",
        kb_type="document",
        question="什么是量子比特？",
        reference_answer="量子计算的基本单元。",
        anchor_doc="seed-1",
        anchor_text="量子比特",
        annotated_at="2026-08-15T00:00:00Z",
    ),
    QAEntry(
        id="qa-2",
        kb_type="document",
        question="什么是叠加态？",
        reference_answer="量子态同时处于多个基态的线性组合。",
        anchor_doc="seed-1",
        anchor_text="叠加态",
        annotated_at="2026-08-15T00:00:00Z",
    ),
]


class _StubJudge:
    """确定性评判：faithfulness 按条目序号递增，其余固定。"""

    def __init__(self) -> None:
        self.calls = 0

    def judge(self, question, answer, contexts, reference):
        self.calls += 1
        return {
            "faithfulness": 0.5 + self.calls * 0.1,
            "answer_relevancy": 0.8,
            "context_precision": 0.7,
            "context_recall": 0.9,
        }


def _search(question, kb_type):
    return [f"上下文({question})", "第二段"]


def _generate(question, contexts):
    return f"答案({question})"


def test_run_eval_passes_through_injected_components():
    judge = _StubJudge()
    records = run_eval(_ENTRIES, _search, _generate, judge)
    assert len(records) == 2
    assert judge.calls == 2
    assert records[0].id == "qa-1"
    assert records[0].answer == "答案(什么是量子比特？)"
    assert records[0].metrics["faithfulness"] == 0.6
    assert records[1].metrics["faithfulness"] == 0.7
    # 四指标齐全
    assert set(records[0].metrics) == {
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    }


def test_run_eval_rejects_missing_metrics():
    class PartialJudge:
        def judge(self, question, answer, contexts, reference):
            return {"faithfulness": 0.5}  # 缺三指标

    try:
        run_eval(_ENTRIES[:1], _search, _generate, PartialJudge())
    except ValueError as exc:
        assert "缺少指标" in str(exc)
    else:
        raise AssertionError("缺失指标应报错")


def test_summarize_averages_metrics():
    records = run_eval(_ENTRIES, _search, _generate, _StubJudge())
    summary = summarize(records)
    assert summary["answer_relevancy"] == 0.8
    assert summary["faithfulness"] == pytest.approx(0.65)  # (0.6 + 0.7) / 2


def test_summarize_rejects_empty():
    try:
        summarize([])
    except ValueError:
        pass
    else:
        raise AssertionError("空记录应报错")


def test_write_report_emits_detail_and_summary(tmp_path):
    records = run_eval(_ENTRIES, _search, _generate, _StubJudge())
    summary = summarize(records)
    path = tmp_path / "ragas-eval.json"
    write_report(records, summary, path)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["faithfulness"] == pytest.approx(0.65)
    detail_path = tmp_path / "ragas-eval.jsonl"
    lines = detail_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row["id"] == "qa-1" and "faithfulness" in row


def _metrics(faithfulness: float) -> dict[str, float]:
    return {
        "faithfulness": faithfulness,
        "answer_relevancy": 0.8,
        "context_precision": 0.7,
        "context_recall": 0.9,
    }


def test_check_baseline_passes_within_tolerance():
    assert check_baseline(_metrics(0.61), _metrics(0.6)) == []


def test_check_baseline_fails_below_tolerance():
    failures = check_baseline(_metrics(0.5), _metrics(0.6))
    assert len(failures) == 1
    assert "faithfulness" in failures[0]
    assert str(TOLERANCE) in failures[0]


def test_check_ragas_deps_reports_missing(monkeypatch):
    # 模拟未安装环境：sys.modules 注入空占位使 import 抛 ImportError
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"ragas", "langchain_openai"}:
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    missing = check_ragas_deps()
    assert set(missing) == {"ragas", "langchain_openai"}