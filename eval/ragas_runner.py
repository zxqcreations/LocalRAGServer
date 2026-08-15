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
from typing import TYPE_CHECKING, Protocol

from eval.dataset import DATASET_VERSION, QAEntry

EVAL_DIR = Path(__file__).resolve().parent
REPORT_ROOT = EVAL_DIR.parent / "docs" / "perf"
BASELINE_PATH = REPORT_ROOT / "ragas-baseline.json"

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
TOLERANCE = 0.05  # 基线容差（与 run_retrieval 同机制）

SearchFn = Callable[[str, str], list[str]]  # (question, kb_type) -> contexts
GenerateFn = Callable[[str, list[str]], str]  # (question, contexts) -> answer

# langchain_core 为可选评测依赖（不进 lock）；CI/无依赖环境用占位基类
# （build_judge 前 check_ragas_deps 已拦截，占位类不会被实际使用）。
# TYPE_CHECKING 分支供 pyright 解析真类型，else 为运行时兜底（标准懒依赖模式）。
if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings  # type: ignore[import-not-found]
else:  # pragma: no cover - 运行时兜底
    try:
        from langchain_core.embeddings import Embeddings
    except ImportError:

        class Embeddings:  # type: ignore[no-redef]
            def embed_documents(self, texts):  # type: ignore[empty-body]
                raise NotImplementedError

            def embed_query(self, text):  # type: ignore[empty-body]
                raise NotImplementedError


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
    entries: list[QAEntry],
    search_fn: SearchFn,
    generate_fn: GenerateFn,
    judge: Judge,
    on_record=None,
) -> list[EvalRecord]:
    """逐条评测：检索 → 生成 → 评判，返回与输入同序的记录列表。

    on_record(record)：每条完成后回调（长评测逐条落盘——中断后可 --offset 续跑，
    已完成的明细不丢失）。
    """
    records: list[EvalRecord] = []
    for entry in entries:
        contexts = search_fn(entry.question, entry.kb_type)
        answer = generate_fn(entry.question, contexts)
        metrics = judge.judge(entry.question, answer, contexts, entry.reference_answer)
        missing = [m for m in METRICS if m not in metrics]
        if missing:
            raise ValueError(f"{entry.id}: 评判结果缺少指标 {missing}")
        record = EvalRecord(
            id=entry.id,
            kb_type=entry.kb_type,
            question=entry.question,
            answer=answer,
            metrics={m: float(metrics[m]) for m in METRICS},
        )
        records.append(record)
        if on_record is not None:
            on_record(record)
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
    """对照基线：任一指标低于 (基线 - 容差) 即失败，返回失败项说明。

    评测集版本不匹配视为不可比（quality.md Phase 5：跨版本结果不可比）。
    """
    baseline_version = baseline.get("dataset_version")
    if baseline_version != DATASET_VERSION:
        return [
            f"评测集版本不匹配：基线 {baseline_version} != 当前 {DATASET_VERSION}（不可比）"
        ]
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


class _LocalEmbeddings(Embeddings):
    """本机 bge-m3 嵌入（sentence-transformers），继承 langchain Embeddings 协议。

    实测注记：llama-server 默认不支持 /v1/embeddings（501），RAGAS 上下文指标
    的嵌入必须走本机模型——与生产检索同源（bge-m3），不引入第二套嵌入。
    """

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

        self._model = SentenceTransformer(model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [
            vec.tolist()
            for vec in self._model.encode(texts, normalize_embeddings=True, batch_size=16)
        ]

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode(
            [text], normalize_embeddings=True
        )[0].tolist()


def build_judge():
    """真实 RAGAS 评判链（懒加载）：LLM 评判指向本地 llama-server，
    嵌入用本机 bge-m3（sentence-transformers，与生产同源）。

    base_url 取环境变量 RAGAS_LLM_BASE_URL（默认 http://127.0.0.1:9001/v1）。
    """
    import os

    # ragas/langchain 为可选评测依赖（不进 lock，docs/design/ragas-eval.md）
    from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
    from pydantic import SecretStr
    from ragas import evaluate  # type: ignore[import-not-found]
    from ragas.metrics import (  # type: ignore[import-not-found]
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )
    from ragas.run_config import RunConfig  # type: ignore[import-not-found]

    base_url = os.environ.get("RAGAS_LLM_BASE_URL", "http://127.0.0.1:9001/v1")
    llm = ChatOpenAI(model="qwen3-8b", base_url=base_url, api_key=SecretStr("local"))
    embeddings = _LocalEmbeddings()
    # max_workers=3：llama-server 4 slots 支持并发评判（单条 ~1min → 3 并行）
    run_config = RunConfig(max_workers=3, timeout=300)

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
            row = result.to_pandas().iloc[0]  # type: ignore[attr-defined]  # ragas Result 运行时提供
            return {m: float(row[m]) for m in METRICS}

    return RagasJudge()


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGAS 评估闭环")
    parser.add_argument("--check-baseline", action="store_true", help="对照基线门禁")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    # 分批执行：单批次限时长（LLM 评判耗时），中断后可用 --offset 续跑
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 条（续跑起点）")
    parser.add_argument("--limit", type=int, default=0, help="最多评测 N 条（0 = 全部）")
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
        entries = load_qa()[args.offset : args.offset + args.limit if args.limit else None]
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

        batch = f"-{args.offset}" if args.offset else ""
        report_path = REPORT_ROOT / f"ragas-eval{batch}-{_date_stamp()}.json"
        detail_path = report_path.with_suffix(".jsonl")

        # 逐条落盘（长评测中断后可 --offset 续跑，已完成明细不丢失）
        def _append(record: EvalRecord) -> None:
            with detail_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {"id": record.id, "kb_type": record.kb_type, **record.metrics},
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        records = run_eval(entries, _search, _generate, judge, on_record=_append)
        summary = summarize(records)
        report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
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
