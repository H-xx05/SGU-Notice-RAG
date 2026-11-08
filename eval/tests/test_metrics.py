"""指标口径的回归测试。

这些数字一旦变了，往期的评估报告就没法纵向比较了。所以这里锁死具体值，
而不是断言 `> 0` 之类的松散条件。
"""

from __future__ import annotations

from runner import metrics as M


def outcome(
    qid: str,
    *,
    category: str = "current_rule",
    as_of: str = "2026-11-08",
    expected: list[str] | None = None,
    stale: bool = False,
    retrieved: list[str] | None = None,
    buckets: list[str] | None = None,
    refused: bool = False,
    refusal_expected: bool = False,
) -> M.QueryOutcome:
    return M.QueryOutcome(
        query_id=qid,
        category=category,
        as_of=as_of,
        expected_doc_ids=expected or [],
        expect_refusal=refusal_expected,
        expect_stale_recall=stale,
        retrieved_doc_ids=retrieved or [],
        retrieved_buckets=buckets or [],
        refused=refused,
    )


class TestStaleRecallRate:
    def test_no_stale_returns_zero(self) -> None:
        outs = [
            outcome("a", expected=["d1"], retrieved=["d1"], buckets=["current"]),
            outcome("b", expected=["d2"], retrieved=["d2"], buckets=["current"]),
        ]
        assert M.stale_recall_rate(outs) == 0.0

    def test_counts_query_not_chunk(self) -> None:
        """一条 query 召回 5 条过期版本，也只算 1 个坏样本。"""
        outs = [
            outcome(
                "a",
                expected=["d1"],
                retrieved=["s1", "s2", "s3", "d1"],
                buckets=["stale", "stale", "stale", "current"],
            ),
            outcome("b", expected=["d2"], retrieved=["d2"], buckets=["current"]),
        ]
        assert M.stale_recall_rate(outs) == 0.5

    def test_empty_retrieval_excluded_from_denominator(self) -> None:
        """没召回任何东西的 query 不算"误召回"，它是拒答题。"""
        outs = [
            outcome("a", expected=["d1"], retrieved=["s1"], buckets=["stale"]),
            outcome("b", refused=True, refusal_expected=True),
        ]
        assert M.stale_recall_rate(outs) == 1.0

    def test_cross_year_trap_excluded_from_denominator(self) -> None:
        """跨年度陷阱题召回旧版本是**正确行为**，不能算错。"""
        outs = [
            outcome("a", expected=["d1"], retrieved=["s1"], buckets=["stale"]),
            outcome(
                "b",
                category="cross_year_trap",
                expected=["d2025"],
                retrieved=["d2025"],
                buckets=["stale"],
                stale=True,
            ),
        ]
        assert M.stale_recall_rate(outs) == 1.0

    def test_all_excluded_returns_none(self) -> None:
        outs = [outcome("a", refused=True, refusal_expected=True)]
        assert M.stale_recall_rate(outs) is None


class TestRefusalRate:
    def test_perfect(self) -> None:
        outs = [
            outcome("a", expected=["d1"], retrieved=["d1"]),
            outcome("b", refused=True, refusal_expected=True),
        ]
        assert M.refusal_rate(outs) == 1.0

    def test_answered_when_should_refuse(self) -> None:
        outs = [
            outcome("a", expected=["d1"], retrieved=["d1"]),
            outcome("b", refused=False, refusal_expected=True, retrieved=["d9"], buckets=["current"]),
        ]
        assert M.refusal_rate(outs) == 0.5

    def test_refused_when_should_answer(self) -> None:
        outs = [outcome("a", expected=["d1"], refused=True)]
        assert M.refusal_rate(outs) == 0.0


class TestContextPrecisionRecall:
    def test_recall_partial(self) -> None:
        outs = [outcome("a", expected=["d1", "d2"], retrieved=["d1", "dx"], buckets=["c", "c"])]
        assert M.context_recall(outs) == 0.5

    def test_precision_penalises_noise(self) -> None:
        outs = [outcome("a", expected=["d1"], retrieved=["d1", "dx"], buckets=["c", "c"])]
        assert M.context_precision(outs) == 0.5

    def test_no_answer_query_scores_zero_precision(self) -> None:
        """无答案题召回了东西 → 全算噪声。"""
        outs = [outcome("a", expected=[], retrieved=["dx"], buckets=["current"])]
        assert M.context_precision(outs) == 0.0

    def test_mrr(self) -> None:
        outs = [
            outcome("a", expected=["d1"], retrieved=["dx", "d1"], buckets=["current", "current"]),
            outcome("b", expected=["d2"], retrieved=["d2"], buckets=["current"]),
        ]
        assert M.mrr(outs) == (0.5 + 1.0) / 2


class TestByYear:
    def test_groups_by_as_of_year_not_doc_year(self) -> None:
        """同一份旧文档，2026 年评算过期，2025 年评是现行。分组必须按 as_of。"""
        outs = [
            outcome(
                "a",
                as_of="2026-11-08",
                expected=["d2025"],
                retrieved=["d2025"],
                buckets=["stale"],
            ),
            outcome(
                "b",
                as_of="2025-06-01",
                expected=["d2025"],
                retrieved=["d2025"],
                buckets=["current"],
            ),
        ]
        rows = M.by_year(outs)
        assert [r["year"] for r in rows] == [2025, 2026]
        assert rows[0]["stale_recall_rate"] == 0.0
        assert rows[1]["stale_recall_rate"] == 1.0

    def test_bad_as_of_goes_to_zero_group(self) -> None:
        outs = [outcome("a", as_of="not-a-date", expected=["d1"], retrieved=["d1"], buckets=["c"])]
        assert M.by_year(outs)[0]["year"] == 0


class TestCompute:
    def test_all_metrics_present(self) -> None:
        m = M.compute([outcome("a", expected=["d1"], retrieved=["d1"], buckets=["current"])])
        assert set(m.as_dict()) == {
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
            "stale_recall_rate",
            "refusal_rate",
        }
        # 生成类指标不由 compute 填，必须是 None（不能是 0.0，那是"算出来就是 0"）
        assert m.faithfulness is None
        assert m.answer_relevancy is None
