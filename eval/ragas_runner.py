"""RAGAS 评估闭环（docs/design/ragas-eval.md）。

核心流程与 RAGAS 解耦：检索/生成/评判全部注入，测试用 stub 离线验证；
真实 RAGAS 评判在 build_judge 中懒加载（依赖不进 lock，缺失时给出安装指引）。

用法：
  uv run python -m eval.ragas_runner                    # 全量评测 + 报告
  uv run python -m eval.ragas_runner --check-baseline   # 对照基线门禁（劣化即非零退出）
"""
import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from eval.dataset import QAEntry

EVAL_DIR = Path(__file__).resolve().parent
REPORT_ROOT = EVAL_DIR.parent / "docs" / "perf"
BASELINE_PATH = REPORT_ROOT / "ragas-baseline.json"

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
TOLERANCE = 0.05  # 基线容差（与 run_retrieval 同机制）

SearchFn = Callable[[str, str], list[str]]  # (question, kb_type) -> contexts
GenerateFn = Callable[[str, list[str]], str]  # (question, contexts) -> answer


class Judge(Protocol):
    def judge(
        self, question: str, answer: str, contexts: list[str], reference: str
    ) -> dict[str, float]: ...


@dataclass(frozen=True)
class EvalRecord:
    id: str
    kb_type: str
    question: str
    answer: str
    metrics: dict[str, float]


def check_ragas_deps() -> list[str]:
    """检查 RAGAS 依赖可导入性，返回缺失包名列表（空 = 就绪）。

    依赖不进 lock（同 torch 策略，docs/design/ragas-eval.md）：
    uv pip install ragas langchain-openai
    """
    missing: list[str] = []
    for package in ("ragas", "langchain_openai"):
        try:
            __import__(package)
        except ImportError:
            missing.append(package)
    return missing


def run_eval(
    entries: list[QAEntry], search_fn: SearchFn, generate_fn: GenerateFn, judge: Judge
) -> list[EvalRecord]:
    """逐条评测：检索 → 生成 → 评判，返回与输入同序的记录列表。"""
    records: list[EvalRecord] = []
    for entry in entries:
        contexts = search_fn(entry.question, entry.kb_type)
        answer = generate_fn(entry.question, contexts)
        metrics = judge.judge(entry.question, answer, contexts, entry.reference_answer)
        missing = [m for m in METRICS if m not in metrics]
        if missing:
            raise ValueError(f"{entry.id}: 评判结果缺少指标 {missing}")
        records.append(
            EvalRecord(
                id=entry.id,
                kb_type=entry.kb_type,
                question=entry.question,
                answer=answer,
                metrics={m: float(metrics[m]) for m in METRICS},
            )
        )
    return records


def summarize(records: list[EvalRecord]) -> dict[str, float]:
    """四指标均值（评测集级汇总）。"""
    if not records:
        raise ValueError("评测记录为空，无法汇总")
    return {m: sum(r.metrics[m] for r in records) / len(records) for m in METRICS}


def write_report(records: list[EvalRecord], summary: dict[str, float], path: Path) -> None:
    """落盘：逐条明细 JSONL + 汇总 JSON（同一路径前缀）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    detail_path = path.with_suffix(".jsonl")
    with detail_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(
                json.dumps(
                    {"id": r.id, "kb_type": r.kb_type, **r.metrics}, ensure_ascii=False
                )
                + "\n"
            )
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def check_baseline(
    summary: dict[str, float], baseline: dict[str, float], tolerance: float = TOLERANCE
) -> list[str]:
    """对照基线：任一指标低于 (基线 - 容差) 即失败，返回失败项说明。"""
    failures = []
    for metric in METRICS:
        floor = baseline.get(metric, 0.0) - tolerance
        if summary[metric] < floor:
            failures.append(
                f"{metric}: {summary[metric]:.3f} < 基线 {baseline[metric]:.3f} - {tolerance}"
            )
    return failures


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, float]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def build_judge():
    """真实 RAGAS 评判链（懒加载）：LLM 评判 + 嵌入均指向本地服务。

    base_url 取环境变量 RAGAS_LLM_BASE_URL（默认 http://127.0.0.1:9001/v1，
    llama-server）；嵌入用本机 sentence-transformers（bge-m3）。
    """
    import os

    # ragas/langchain 为可选评测依赖（不进 lock，docs/design/ragas-eval.md）
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # type: ignore[import-not-found]
    from ragas import evaluate  # type: ignore[import-not-found]
    from ragas.metrics import (  # type: ignore[import-not-found]
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )
    from ragas.run_config import RunConfig  # type: ignore[import-not-found]

    base_url = os.environ.get("RAGAS_LLM_BASE_URL", "http://127.0.0.1:9001/v1")
    llm = ChatOpenAI(model="qwen3-8b", base_url=base_url, api_key="local")
    embeddings = OpenAIEmbeddings(model="bge-m3", base_url=base_url, api_key="local")
    run_config = RunConfig(max_workers=1, timeout=300)

    class RagasJudge:
        def judge(self, question, answer, contexts, reference):
            # RAGAS evaluate 按 dataset 批量，逐条包一层兼容 Judge Protocol
            from datasets import Dataset  # type: ignore[import-not-found]

            result = evaluate(
                Dataset.from_dict(
                    {
                        "question": [question],
                        "answer": [answer],
                        "contexts": [contexts],
                        "ground_truth": [reference],
                    }
                ),
                metrics=[
                    faithfulness,
                    answer_relevancy,
                    context_precision,
                    context_recall,
                ],
                llm=llm,
                embeddings=embeddings,
                run_config=run_config,
            )
            row = result.to_pandas().iloc[0]
            return {m: float(row[m]) for m in METRICS}

    return RagasJudge()


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGAS 评估闭环")
    parser.add_argument("--check-baseline", action="store_true", help="对照基线门禁")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args()

    missing = check_ragas_deps()
    if missing:
        print(
            f"缺少评测依赖：{', '.join(missing)}。"
            "安装：uv pip install ragas langchain-openai（不进 lock，见 docs/design/ragas-eval.md）"
        )
        return 2

    from apps.api.main import create_app  # 复用生产配置（Settings/检索/生成同路径）
    from core.config import get_settings
    from eval.dataset import load_qa

    settings = get_settings()
    app = create_app(settings)
    # TestClient 上下文驱动 lifespan（直用 app.state 组件，不经 HTTP，同生产路径）
    from fastapi.testclient import TestClient

    with TestClient(app) as _client:
        entries = load_qa()
        judge = build_judge()
        # 评测 KB 命名契约：eval-<kb_type>（fixtures 入库脚本按此命名）
        kb_ids = {
            kb_type: _resolve_kb_id(app.state.registry, f"eval-{kb_type}")
            for kb_type in {e.kb_type for e in entries}
        }

        def _search(question: str, kb_type: str) -> list[str]:
            results = app.state.search_service.search(kb_ids[kb_type], question)
            return [r.content for r in results]

        def _generate(question: str, contexts: list[str]) -> str:
            from core.generation.llm import build_rag_messages

            return app.state.chat_client.chat(build_rag_messages(question, contexts)).content

        records = run_eval(entries, _search, _generate, judge)
        summary = summarize(records)
        report_path = REPORT_ROOT / f"ragas-eval-{_date_stamp()}.json"
        write_report(records, summary, report_path)
        print(f"评测完成：{len(records)} 条 → {report_path}")
        for metric, value in summary.items():
            print(f"  {metric}: {value:.3f}")
        if args.check_baseline:
            failures = check_baseline(summary, load_baseline(args.baseline))
            if failures:
                print("基线门禁失败：")
                for f in failures:
                    print(f"  - {f}")
                return 1
            print("基线门禁通过")
    return 0


def _resolve_kb_id(registry, name: str) -> str:
    for kb in registry.list_kbs():
        if kb.name == name:
            return kb.id
    raise SystemExit(
        f"评测 KB「{name}」不存在：请先用 scripts/import_docs.py 将 "
        "eval/fixtures/docs 入库（或本地实测前完成数据准备）"
    )


def _date_stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


if __name__ == "__main__":
    sys.exit(main())
