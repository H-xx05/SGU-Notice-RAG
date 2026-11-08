"""时效性判定的单元测试——这是本项目最核心的测试文件。

每条用例都对应 docs/prd.md 的一个风险点，尤其是"跨年度陷阱"。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import time_filter
from app.schemas import TimeFilterBody


class TestJudge:
    def test_current_within_window(self):
        d = time_filter.judge(
            effective_from="2026-09-08", effective_to="2027-01-10", as_of=date(2026, 11, 8)
        )
        assert d.bucket == "current"
        assert d.is_current and not d.is_stale

    def test_boundary_day_still_valid(self):
        """effective_to 当天仍然有效（"某日止"含当天）。"""
        d = time_filter.judge(
            effective_from="2026-09-08", effective_to="2026-11-08", as_of=date(2026, 11, 8)
        )
        assert d.bucket == "current"

    def test_day_after_expiry_is_stale(self):
        d = time_filter.judge(
            effective_from="2026-09-08", effective_to="2026-11-07", as_of=date(2026, 11, 8)
        )
        assert d.bucket == "stale"

    def test_open_ended_is_current(self):
        """没有 effective_to 的规定长期有效。"""
        d = time_filter.judge(
            effective_from="2024-06-01", effective_to=None, as_of=date(2026, 11, 8)
        )
        assert d.bucket == "current"

    def test_not_yet_effective_is_stale(self):
        """生效日在未来 = 现在不能用，判 stale 而不是 current。"""
        d = time_filter.judge(
            effective_from="2026-12-01", effective_to="2027-01-15", as_of=date(2026, 11, 8)
        )
        assert d.bucket == "stale"

    def test_missing_dates_fall_back_to_publish(self):
        d = time_filter.judge(
            effective_from=None,
            effective_to=None,
            publish_date="2026-03-04",
            as_of=date(2026, 11, 8),
        )
        assert d.bucket == "current"

    def test_publish_in_future_is_unknown(self):
        d = time_filter.judge(
            effective_from=None,
            effective_to=None,
            publish_date="2026-12-04",
            as_of=date(2026, 11, 8),
        )
        assert d.bucket == "unknown"

    def test_all_missing_is_unknown(self):
        """什么都缺就判 unknown，绝不默认当现行——这是幻觉的第一来源。"""
        d = time_filter.judge(effective_from=None, effective_to=None, as_of=date(2026, 11, 8))
        assert d.bucket == "unknown"

    def test_invalid_date_is_unknown(self):
        d = time_filter.judge(
            effective_from="2026/09/08", effective_to="2027-01-10", as_of=date(2026, 11, 8)
        )
        assert d.bucket == "unknown"

    def test_penalty_factor(self):
        stale = time_filter.judge(effective_from="2025-01-01", effective_to="2025-06-30")
        current = time_filter.judge(effective_from="2026-01-01", effective_to=None)
        assert stale.penalty_factor == 0.5
        assert current.penalty_factor == 1.0


class TestResolveWindow:
    def test_off_returns_none(self):
        assert time_filter.resolve_window(TimeFilterBody(mode="off")) is None

    def test_auto_returns_none(self):
        assert time_filter.resolve_window(TimeFilterBody(mode="auto"), date(2026, 11, 8)) is None

    def test_range_ok(self):
        w = time_filter.resolve_window(
            TimeFilterBody(mode="range", date_from="2024-01-01", date_to="2024-12-31")
        )
        assert w == (date(2024, 1, 1), date(2024, 12, 31))

    def test_range_missing_bound_raises(self):
        with pytest.raises(ValueError):
            time_filter.resolve_window(TimeFilterBody(mode="range", date_from="2024-01-01"))

    def test_range_reversed_raises(self):
        with pytest.raises(ValueError):
            time_filter.resolve_window(
                TimeFilterBody(mode="range", date_from="2024-12-31", date_to="2024-01-01")
            )


class TestPayloadFilter:
    def test_current_filter_shape(self):
        f = time_filter.current_payload_filter(date(2026, 11, 8))
        assert f["must"][0]["key"] == "effective_from"
        assert f["must"][0]["range"]["lte"] == "2026-11-08"
        # 关键的长期有效性分支：没有 effective_to 的也必须保留
        assert any("is_empty" in c for c in f["should"])

    def test_range_filter_shape(self):
        f = time_filter.range_payload_filter((date(2024, 1, 1), date(2024, 12, 31)))
        assert f is not None
        assert f["must"][0]["range"]["lte"] == "2024-12-31"
        assert f["should"][0]["range"]["gte"] == "2024-01-01"

    def test_none_window(self):
        assert time_filter.range_payload_filter(None) is None
