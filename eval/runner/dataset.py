"""读 queries.jsonl，做**严格**校验。

宁可启动即失败，也不要在跑了 40 条 query 之后才发现 dataset 里少了个字段
—— 那时候你分不清是模型不行还是数据不行。
"""

from __future__ import annotations

import json
from pathlib import Path

from .metrics import QueryOutcome

REQUIRED = ("query_id", "query", "as_of", "expected_doc_ids", "expect_stale_recall", "category")
CATEGORIES = {
    "current_rule",       # 问现行规定
    "historical",         # 问历史某一时点（as_of 非今天）
    "cross_year_trap",    # 跨年度陷阱：新旧版本语义相近，必须按 as_of 判
    "no_answer",          # 语料里没有答案，应拒答
}


class DatasetError(ValueError):
    pass


def load(path: Path) -> list[dict]:
    if not path.is_file():
        raise DatasetError(f"数据集不存在：{path}")

    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}:{lineno} 不是合法 JSON：{exc}") from exc

    if not rows:
        raise DatasetError(f"{path} 是空的")

    _validate(rows, path)
    return rows


def _validate(rows: list[dict], path: Path) -> None:
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        where = f"{path}#{i}"
        missing = [k for k in REQUIRED if k not in row]
        if missing:
            raise DatasetError(f"{where} 缺少字段：{', '.join(missing)}")

        qid = row["query_id"]
        if qid in seen:
            raise DatasetError(f"{where} query_id 重复：{qid}")
        seen.add(qid)

        if row["category"] not in CATEGORIES:
            raise DatasetError(f"{where} category 不认识：{row['category']}")
        if not str(row["query"]).strip():
            raise DatasetError(f"{where} query 为空")
        if not isinstance(row["expected_doc_ids"], list):
            raise DatasetError(f"{where} expected_doc_ids 必须是数组")
        if not isinstance(row["expect_stale_recall"], bool):
            raise DatasetError(f"{where} expect_stale_recall 必须是布尔值")

        # 无答案题必须 expect_refusal:true，否则指标口径自相矛盾
        if row["category"] == "no_answer" and not row.get("expect_refusal"):
            raise DatasetError(f"{where} 无答案题必须写 expect_refusal: true")

    # 这是本项目的立项理由，缺了就没在测核心问题
    traps = [r for r in rows if r["category"] == "cross_year_trap"]
    if not traps:
        raise DatasetError(
            f"{path} 里没有 cross_year_trap 题。项目的核心指标是过期误召回率，"
            "数据集里必须有跨年度语义相近的题，否则评估没有意义。"
        )
    no_answer = [r for r in rows if r["category"] == "no_answer"]
    if not no_answer:
        raise DatasetError(f"{path} 里没有 no_answer 题，拒答能力无法评估。")


def to_outcomes(rows: list[dict]) -> list[QueryOutcome]:
    """把数据集行转成待填观测的 outcome。"""
    return [
        QueryOutcome(
            query_id=r["query_id"],
            category=r["category"],
            as_of=r["as_of"],
            expected_doc_ids=list(r["expected_doc_ids"]),
            expect_refusal=bool(r.get("expect_refusal", False)),
            expect_stale_recall=bool(r["expect_stale_recall"]),
        )
        for r in rows
    ]
