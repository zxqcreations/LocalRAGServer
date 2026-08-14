"""评测集加载与校验（审计 F9/ARC-015）。

设计要点：
- 锚定文本子串而非 chunk id：分块内容确定，但 chunk id 每次摄取随机（uuid），
  用 anchor_text 子串匹配才可跨运行回归。
- 种子语料随评测集入库（eval/fixtures/docs/），任何评测结果记录评测集版本，
  跨版本不可比。
- 污染隔离：线上真实查询不得写入本评测集（Phase 5 起单独集合 + 定期抽查）。
"""
import json
from pathlib import Path

from pydantic import BaseModel, Field

EVAL_DIR = Path(__file__).resolve().parent
FIXTURES_ROOT = EVAL_DIR / "fixtures" / "docs"
DATASET_PATH = EVAL_DIR / "datasets" / "qa.jsonl"

KB_TYPES = {"document", "code", "web"}
MIN_ENTRIES = 50  # 审计 F9：Day 1 种子 ≥50 条
MAX_ANCHOR_LEN = 40  # 锚点必须落在单个 chunk 内（分块尺寸 150 起，留足余量）


class QAEntry(BaseModel):
    id: str
    kb_type: str
    question: str = Field(min_length=1)
    reference_answer: str = Field(min_length=1)
    anchor_doc: str
    anchor_text: str = Field(min_length=1)
    is_hard: bool = False
    annotated_by: str = "seed"
    annotated_at: str


def load_qa(path: Path = DATASET_PATH) -> list[QAEntry]:
    """加载评测集（JSONL，每行一条）。"""
    entries = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(QAEntry.model_validate(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"评测集第 {lineno} 行解析失败：{exc}") from exc
    return entries


def fixture_stems(kb_type: str) -> set[str]:
    """指定类型的种子文档 stem 集合。"""
    root = FIXTURES_ROOT / kb_type
    if not root.is_dir():
        return set()
    return {p.stem for p in root.iterdir() if p.is_file()}


def _fixture_content(kb_type: str, stem: str) -> str | None:
    for p in (FIXTURES_ROOT / kb_type).iterdir():
        if p.stem == stem and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    return None


def validate_dataset(entries: list[QAEntry]) -> list[str]:
    """校验评测集自洽性，返回错误列表（空列表 = 通过）。"""
    errors: list[str] = []
    if len(entries) < MIN_ENTRIES:
        errors.append(f"条目数不足：{len(entries)} < {MIN_ENTRIES}")
    seen: set[str] = set()
    for e in entries:
        if e.id in seen:
            errors.append(f"重复 id：{e.id}")
        seen.add(e.id)
        if e.kb_type not in KB_TYPES:
            errors.append(f"{e.id}: 未知 kb_type {e.kb_type}")
            continue
        if e.anchor_doc not in fixture_stems(e.kb_type):
            errors.append(f"{e.id}: anchor_doc {e.anchor_doc} 不在 {e.kb_type} 种子语料中")
            continue
        if len(e.anchor_text) > MAX_ANCHOR_LEN:
            errors.append(
                f"{e.id}: anchor_text 过长（{len(e.anchor_text)} > {MAX_ANCHOR_LEN}），"
                "可能跨 chunk 边界导致无法命中"
            )
        content = _fixture_content(e.kb_type, e.anchor_doc)
        if content is None or e.anchor_text not in content:
            errors.append(f"{e.id}: anchor_text 在 {e.anchor_doc} 中不存在（锚定失效）")
    return errors
