"""分块与版本链的判定表。

分块错→检索精度直接崩；版本链错→**旧版本被当成现行版**，这正是本项目要防的
时效性幻觉。两者都必须有回归保护。
"""

from __future__ import annotations

from ingestion.chunking.splitter import CHUNK_MAX, split_into_chunks
from ingestion.doc_registry import (
    assign_versions,
    link_versions,
    make_doc_id,
    mentions_supersede,
    topic_key_of,
)


class TestSplitter:
    def test_every_chunk_carries_title(self) -> None:
        """首行必须带标题：切碎了之后，只看正文片段无法判断是哪份通知。"""
        paras = [f"第{i}条 这是第{i}条的内容，需要足够长以形成独立小节。" for i in range(1, 6)]
        chunks = split_into_chunks("关于做好转专业工作的通知", paras)
        assert chunks
        for c in chunks:
            assert c.text.startswith("关于做好转专业工作的通知")

    def test_section_path_from_numbering(self) -> None:
        paras = [
            "一、报名条件",
            "全日制本科一年级、二年级学生，已修课程平均学分绩点不低于2.5，且无违纪记录。",
            "二、报名安排",
            "报名时间为2026年3月4日至2026年5月31日，逾期不再受理。",
        ]
        chunks = split_into_chunks("关于转专业的通知", paras)
        paths = {c.section_path for c in chunks}
        assert any("报名条件" in p for p in paths)
        assert any("报名安排" in p for p in paths)

    def test_no_chunk_exceeds_max(self) -> None:
        long_para = "。" + "很长的句子" * 300 + "。"
        chunks = split_into_chunks("标题", [long_para])
        for c in chunks:
            assert len(c.text) <= CHUNK_MAX, f"chunk 超长 {len(c.text)}"

    def test_short_doc_yields_one_chunk(self) -> None:
        chunks = split_into_chunks("标题", ["一句话通知。"])
        assert len(chunks) == 1

    def test_no_empty_text(self) -> None:
        chunks = split_into_chunks("标题", ["", "  ", "正文。"])
        assert all(c.text.strip() for c in chunks)


class TestTopicKey:
    def test_year_and_term_are_stripped(self) -> None:
        """2025 版与 2026 版必须落到同一个 topic_key，否则串不成版本链。"""
        a = topic_key_of("关于做好2025年转专业工作的通知")
        b = topic_key_of("关于做好2026年转专业工作的通知")
        assert a == b

    def test_school_year_stripped(self) -> None:
        a = topic_key_of("关于2025-2026学年第一学期期末考试安排的通知")
        b = topic_key_of("关于2026-2027学年第一学期期末考试安排的通知")
        assert a == b

    def test_different_topics_differ(self) -> None:
        assert topic_key_of("关于转专业工作的通知") != topic_key_of("关于期末考试安排的通知")


class TestDocId:
    def test_format(self) -> None:
        """`jwb-jxtz-{year}-{news_id}`：用站点 news_id，重跑幂等。"""
        assert make_doc_id(2026, "29459") == "jwb-jxtz-2026-29459"

    def test_stable_across_calls(self) -> None:
        assert make_doc_id(2026, "29459") == make_doc_id(2026, "29459")


class TestMentionsSupersede:
    def test_positive(self) -> None:
        assert mentions_supersede("原《韶关学院学生学籍管理规定》同时废止。")
        assert mentions_supersede("本办法自发布之日起施行，原办法不再执行。")

    def test_negative(self) -> None:
        assert not mentions_supersede("现就有关事项通知如下。")
        assert not mentions_supersede("")


class TestVersionChain:
    """`assign_versions` 吃 `[(news_id, title, publish_date)]`，返回同序 DocMeta。"""

    def test_assign_versions_increments_within_topic(self) -> None:
        metas = assign_versions([
            ("100", "关于做好2025年转专业工作的通知", "2025-03-01"),
            ("200", "关于做好2026年转专业工作的通知", "2026-03-04"),
        ])
        assert metas[0].doc_version == "v2025.1"
        assert metas[1].doc_version == "v2026.1"
        assert metas[0].doc_version_rank == 2025 * 100 + 1
        assert metas[1].doc_version_rank == 2026 * 100 + 1
        # 年份不同 → topic_key 相同（年份已剥离），所以是同一个主题的两个版本
        assert metas[0].topic_key == metas[1].topic_key

    def test_rank_is_monotonic_across_years(self) -> None:
        metas = assign_versions([
            ("200", "关于做好2026年转专业工作的通知", "2026-03-04"),
            ("100", "关于做好2025年转专业工作的通知", "2025-03-01"),
        ])
        by_id = {m.doc_id: m for m in metas}
        assert (
            by_id["jwb-jxtz-2026-200"].doc_version_rank
            > by_id["jwb-jxtz-2025-100"].doc_version_rank
        )

    def test_only_latest_is_current(self) -> None:
        metas = assign_versions([
            ("100", "关于做好2025年转专业工作的通知", "2025-03-01"),
            ("200", "关于做好2026年转专业工作的通知", "2026-03-04"),
        ])
        link_versions(metas, {
            m.doc_id: t for m, t in zip(
                metas,
                ["原通知同时废止。", "现就2026年转专业工作通知如下。"],
                strict=True,
            )
        })
        by_id = {m.doc_id: m for m in metas}
        # 2026 版正文没写"废止"，按"宁可多留"原则，2025 版保持 is_current=True
        assert by_id["jwb-jxtz-2025-100"].is_current is True

    def test_supersedes_when_newer_says_abolished(self) -> None:
        """新版正文显式写"原…废止"时才切断旧版。"""
        metas = assign_versions([
            ("100", "韶关学院学生学籍管理规定", "2024-06-01"),
            ("200", "韶关学院学生学籍管理规定", "2026-09-01"),
        ])
        texts = {
            metas[0].doc_id: "第一条 为规范学籍管理，制定本规定。",
            metas[1].doc_id: "原《韶关学院学生学籍管理规定》同时废止。",
        }
        link_versions(metas, texts)
        by_id = {m.doc_id: m for m in metas}
        assert by_id["jwb-jxtz-2026-200"].supersedes_doc_id == "jwb-jxtz-2024-100"
        assert by_id["jwb-jxtz-2024-100"].is_current is False

    def test_supersedes_points_to_adjacent_version(self) -> None:
        """连续三版：3→2、2→1，链可回溯，而不是 3→1。"""
        metas = assign_versions([
            ("100", "韶关学院学生学籍管理规定", "2024-06-01"),
            ("200", "韶关学院学生学籍管理规定", "2025-06-01"),
            ("300", "韶关学院学生学籍管理规定", "2026-06-01"),
        ])
        texts = {
            metas[0].doc_id: "第一条 制定本规定。",
            metas[1].doc_id: "原规定同时废止。",
            metas[2].doc_id: "原规定同时废止。",
        }
        link_versions(metas, texts)
        by_id = {m.doc_id: m for m in metas}
        assert by_id["jwb-jxtz-2025-200"].supersedes_doc_id == "jwb-jxtz-2024-100"
        assert by_id["jwb-jxtz-2026-300"].supersedes_doc_id == "jwb-jxtz-2025-200"
        assert by_id["jwb-jxtz-2024-100"].is_current is False
        assert by_id["jwb-jxtz-2025-200"].is_current is False
        assert by_id["jwb-jxtz-2026-300"].is_current is True

    def test_texts_as_list_is_supported(self) -> None:
        """`texts` 也接受等长列表（按 metas 下标取）。"""
        metas = assign_versions([
            ("100", "韶关学院学生学籍管理规定", "2024-06-01"),
            ("200", "韶关学院学生学籍管理规定", "2026-09-01"),
        ])
        link_versions(metas, ["第一条 制定本规定。", "原规定同时废止。"])
        assert metas[0].is_current is False
