"""时效性抽取的判定表。

这里每一个用例都对应一条**真实的**通知表述（2026-11 抽查样本），而不是
abstract 的正则测试。原则：没有显式表述就必须返回 `unknown`——预想不到的
"聪明"推断是时效性幻觉的主要来源。
"""

from __future__ import annotations

from ingestion.temporal import UNKNOWN, extract

SAMPLE_TEXT_1 = (
    "第十六条 学生可以在第一学年、第二学年申请转专业，每名学生只能申请一次。"
    "本规定自2024年6月1日起施行。"
)

SAMPLE_TEXT_2 = (
    "一、申请条件\n全日制本科一年级、二年级学生，已修课程平均学分绩点不低于2.5。\n"
    "二、报名安排\n报名时间为2026年3月4日至2026年5月31日。"
)

SAMPLE_TEXT_3 = (
    "请各单位于11月6日（星期五）上午12:00前将附件1纸质盖章版交至校学位办（厚德楼203室）。"
)


class TestExplicitRange:
    def test_range_with_day_precision(self) -> None:
        p = extract(SAMPLE_TEXT_2, publish_date="2026-03-01")
        assert p.from_ == "2026-03-04"
        assert p.to == "2026-05-31"
        assert p.precision == "day"

    def test_range_iso_format(self) -> None:
        p = extract("受理期为2026-03-04至2026-05-31。")
        assert p.from_ == "2026-03-04"
        assert p.to == "2026-05-31"

    def test_range_reversed_is_ignored(self) -> None:
        """起止颠倒的是脏数据，不猜——回到 unknown。"""
        p = extract("受理期为2026年9月1日至2026年3月4日。")
        assert p.precision in ("unknown", "day")
        assert not (p.from_ and p.to and p.from_ > p.to)


class TestFromOnly:
    def test_from_with_since_phrase(self) -> None:
        p = extract(SAMPLE_TEXT_1, publish_date="2024-06-01")
        assert p.from_ == "2024-06-01"
        assert p.to is None
        assert p.precision == "day"

    def test_from_relative_to_publish_date(self) -> None:
        """`自发布之日起执行` 是显式相对表述，起点用发布日期，不算猜。"""
        p = extract("本通知自发布之日起执行。", publish_date="2026-09-08")
        assert p.from_ == "2026-09-08"

    def test_relative_without_publish_date_stays_unknown(self) -> None:
        p = extract("本通知自发布之日起执行。", publish_date=None)
        assert p.from_ is None


class TestToOnly:
    def test_deadline_only(self) -> None:
        p = extract("报名截止时间为2026年4月10日17:00，逾期不再受理。")
        assert p.to == "2026-04-10"
        assert p.from_ is None  # 只知道结束，不知道开始

    def test_valid_until(self) -> None:
        p = extract("本文件有效期至2026年12月31日。")
        assert p.to == "2026-12-31"

    def test_deadline_without_year_is_not_guessed(self) -> None:
        """`请于11月6日前` 没有年份——不推断成今年。"""
        p = extract(SAMPLE_TEXT_3, publish_date="2026-11-02")
        assert p.to is None, "缺年份的日期不得猜成当年"


class TestUnknown:
    def test_plain_notice(self) -> None:
        p = extract("现就有关事项通知如下：请各学院按时报送材料。")
        assert p is UNKNOWN
        assert p.precision == "unknown"
        assert p.from_ is None and p.to is None

    def test_empty(self) -> None:
        assert extract("") is UNKNOWN

    def test_no_sentinel_dates(self) -> None:
        """禁止用 1970-01-01 之类的哨兵表达未知。"""
        p = extract("关于某项工作的通知。")
        assert p.from_ != "1970-01-01"
        assert p.to != "1970-01-01"


class TestPrecision:
    def test_term_hint(self) -> None:
        p = extract("本学期所有课程考核安排见附件。")
        assert p.precision == "term"
        assert p.from_ is None and p.to is None

    def test_month_only(self) -> None:
        p = extract("2026年9月开展教学检查。")
        assert p.precision == "month"

    def test_long_term(self) -> None:
        p = extract("本规定长期有效。")
        assert p.precision == "day"
        assert p.from_ is None and p.to is None


class TestAsDict:
    def test_contract_shape(self) -> None:
        """`as_dict()` 必须正好是契约要求的三个键。"""
        d = extract(SAMPLE_TEXT_2).as_dict()
        assert set(d) == {"from", "to", "precision"}
