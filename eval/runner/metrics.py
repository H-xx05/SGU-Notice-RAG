"""评估指标 —— 纯标准库实现，不依赖 RAGAS。

为什么不用 RAGAS 直接算全部：
  - `stale_recall_rate` 是本项目的**第一指标**，RAGAS 没有这个概念（它只管语义），
    必须自己按 `recency_bucket` 算。
  - RAGAS 的 faithfulness 需要 LLM judge，跑一次要钱要网；CI 里不能跑。
所以分两层：
  - 本文件：确定性的检索/时效指标，CI 每次跑；
  - ragas_adapter.py：生成质量指标，本地按需跑，结果写回同一份报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class QueryOutcome:
    """一条 query 的原始观测。所有指标都从这里算，不重复调接口。"""

    query_id: str
    category: str
    as_of: str
    expected_doc_ids: list[str]
    expect_refusal: bool
    expect_stale_recall: bool

    retrieved_doc_ids: list[str] = field(default_factory=list)
    retrieved_buckets: list[str] = field(default_factory=list)
    refused: bool = False
    refuse_reason: str | None = None
    answer: str = ""

    @property
    def stale_hits(self) -> int:
        """召回到的过期版本条数（recency_bucket == 'stale'）。"""
        return sum(1 for b in self.retrieved_buckets if b == "stale")

    @property
    def hit(self) -> bool:
        """是否召回到至少一条期望文档。"""
        return any(d in self.expected_doc_ids for d in self.retrieved_doc_ids)

    @property
    def first_hit_rank(self) -> int | None:
        """第一个命中期望文档的名次（1-based），没命中为 None。"""
        for i, d in enumerate(self.retrieved_doc_ids, start=1):
            if d in self.expected_doc_ids:
                return i
        return None


@dataclass
class Metrics:
    """一份可序列化的指标快照。字段名与 docs/api.md §5 的 metrics 对齐。"""

    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    stale_recall_rate: float | None = None
    refusal_rate: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return {
            "faithfulness": self.faithfulness,
            "answer_relevancy": self.answer_relevancy,
            "context_precision": self.context_precision,
            "context_recall": self.context_recall,
            "stale_recall_rate": self.stale_recall_rate,
            "refusal_rate": self.refusal_rate,
        }


def stale_recall_rate(outcomes: list[QueryOutcome]) -> float | None:
    """过期误召回率。

    口径（**改动前先想清楚**）：分母是"所有召回了内容的 query"；分子是
    "至少召回了一条已过期版本、且该 query 并不期待过期内容的 query 数"。

    为什么分母不包含"期望召回过期版本"的 query（跨年度陷阱题）：
    那类题召回旧版本是**正确行为**，算进错误率会把模型改得不敢回历史问题。
    """
    considered = [
        o for o in outcomes if o.retrieved_doc_ids and not o.expect_stale_recall
    ]
    if not considered:
        return None
    bad = sum(1 for o in considered if o.stale_hits > 0)
    return bad / len(considered)


def refusal_rate(outcomes: list[QueryOutcome]) -> float | None:
    """拒答准确率：该拒的拒了 + 不该拒的没拒，占全部可判定 query 的比例。"""
    judged = [o for o in outcomes if o.expect_refusal or o.expected_doc_ids]
    if not judged:
        return None
    correct = sum(1 for o in judged if o.refused == o.expect_refusal)
    return correct / len(judged)


def context_recall(outcomes: list[QueryOutcome]) -> float | None:
    """召回率：期望文档里被召回的比例（按 query 平均）。无期望文档的题不计入。"""
    judged = [o for o in outcomes if o.expected_doc_ids]
    if not judged:
        return None
    scores = []
    for o in judged:
        want = set(o.expected_doc_ids)
        got = want & set(o.retrieved_doc_ids)
        scores.append(len(got) / len(want))
    return sum(scores) / len(scores)


def context_precision(outcomes: list[QueryOutcome]) -> float | None:
    """精确率：召回结果里有多少条是"该来的"。

    按位置加权（MRR 风格）会更严格，但这里用简单的 hit-ratio：
    用户看的是前 8 条，只要前 8 条里混进无关文档就要扣分。
    """
    judged = [o for o in outcomes if o.retrieved_doc_ids]
    if not judged:
        return None
    scores = []
    for o in judged:
        want = set(o.expected_doc_ids)
        if not want:
            # 无答案题：任何召回都是噪声
            scores.append(0.0)
            continue
        hits = sum(1 for d in o.retrieved_doc_ids if d in want)
        scores.append(hits / len(o.retrieved_doc_ids))
    return sum(scores) / len(scores)


def mrr(outcomes: list[QueryOutcome]) -> float | None:
    """MRR：第一条命中期望文档的名次倒数。用来看排序质量，不对外展示。"""
    judged = [o for o in outcomes if o.expected_doc_ids]
    if not judged:
        return None
    scores = []
    for o in judged:
        rank = o.first_hit_rank
        scores.append(0.0 if rank is None else 1.0 / rank)
    return sum(scores) / len(scores)


def compute(outcomes: list[QueryOutcome]) -> Metrics:
    """算全部确定性指标。生成类指标（faithfulness 等）由 ragas_adapter 回填。"""
    return Metrics(
        context_precision=context_precision(outcomes),
        context_recall=context_recall(outcomes),
        stale_recall_rate=stale_recall_rate(outcomes),
        refusal_rate=refusal_rate(outcomes),
    )


def by_year(outcomes: list[QueryOutcome]) -> list[dict[str, object]]:
    """按 as_of 的年份分组看 stale_recall_rate —— 新版通知刚发布时最容易翻车。

    注意分组依据是 **query 的评估基准日**，不是文档年份：同一份旧文档，
    在 2026 年评估算过期，在 2025 年评估就是现行。混用会让报表失去意义。
    """
    groups: dict[int, list[QueryOutcome]] = {}
    for o in outcomes:
        groups.setdefault(_year_of(o), []).append(o)
    rows = []
    for year in sorted(groups):
        items = groups[year]
        rows.append(
            {
                "year": year,
                "stale_recall_rate": stale_recall_rate(items),
                "count": len(items),
            }
        )
    return rows


def _year_of(o: QueryOutcome) -> int:
    """取评估基准日的年份；as_of 缺失或格式不对时回退到 0（报表里单独一组）。"""
    try:
        return int(o.as_of[:4])
    except (ValueError, TypeError, IndexError):
        return 0
