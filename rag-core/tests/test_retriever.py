"""检索链路的集成测试：用 fixture store 跑通"过滤→融合→精排→降权"。

这些用例守住的是**行为契约**（谁该排第一、过期版该不该压下去），
而不是具体分数——分数随模型变，行为不该变。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.retriever import retrieve, rrf_fuse
from app.schemas import Chunk, RetrieveRequest, TimeFilterBody
from app.store import FixtureStore, Scored

FIXTURE = Path(__file__).parent / "fixtures" / "chunks.jsonl"
AS_OF = date(2026, 11, 8)


@pytest.fixture(scope="module")
def store() -> FixtureStore:
    return FixtureStore.from_jsonl(FIXTURE)


def _query(q: str, **kw) -> RetrieveRequest:
    return RetrieveRequest(query=q, **kw)


def _ids(resp) -> list[str]:
    return [c.doc_id for c in resp.chunks]


def _score_of(resp, doc_id: str) -> float | None:
    for ch, s in zip(resp.chunks, resp.scores, strict=True):
        if ch.doc_id == doc_id:
            return s
    return None


class TestGoldenOrdering:
    def test_current_version_ranks_above_stale_version(self, store):
        """核心用例：问转专业条件，2026 版必须排在 2025 版前面。"""
        resp = retrieve(
            _query("转专业需要什么条件", time_filter=TimeFilterBody(mode="off"), top_k=20),
            store,
            as_of=AS_OF,
        )
        ids = _ids(resp)
        assert "jwb-jxtz-2026-29459" in ids, f"现行版未召回：{ids}"
        if "jwb-jxtz-2025-29001" in ids:
            assert ids.index("jwb-jxtz-2026-29459") < ids.index("jwb-jxtz-2025-29001"), (
                f"旧版本排在现行版之前：{ids}"
            )

    def test_stale_chunk_gets_penalised(self, store):
        """off 模式下两版都能召回，但过期的那条分数应被腰斩。"""
        resp = retrieve(
            _query("转专业申请条件", top_k=20, time_filter=TimeFilterBody(mode="off")),
            store,
            as_of=AS_OF,
        )
        ids = _ids(resp)
        assert "jwb-jxtz-2025-29001" in ids, f"off 模式应看到旧版本：{ids}"
        assert "jwb-jxtz-2026-29459" in ids, f"off 模式应看到现行版：{ids}"

        old = _score_of(resp, "jwb-jxtz-2025-29001")
        cur = _score_of(resp, "jwb-jxtz-2026-29459")
        assert old is not None and cur is not None
        assert old < cur, f"过期版未降权: {old} vs {cur}"

    def test_auto_mode_filters_future_notice(self, store):
        """2026-12-01 才生效的辅修通知，在 11-08 不应出现在 auto 结果里。"""
        resp = retrieve(
            _query("辅修专业报名条件", time_filter=TimeFilterBody(mode="auto")), store, as_of=AS_OF
        )
        assert "jwb-jxtz-2026-29701" not in _ids(resp), f"未生效通知被召回：{_ids(resp)}"

    def test_off_mode_can_see_future_notice(self, store):
        """off 模式是给 E 做对照实验用的，必须能看见全部数据。"""
        resp = retrieve(
            _query("辅修专业报名条件", top_k=20, time_filter=TimeFilterBody(mode="off")),
            store,
            as_of=AS_OF,
        )
        assert "jwb-jxtz-2026-29701" in _ids(resp), f"off 模式应看到未生效通知：{_ids(resp)}"

    def test_range_mode_returns_historical(self, store):
        """查 2025 年（历史区间）应能召回 2025 版，且不混入区间外的版本。"""
        resp = retrieve(
            _query(
                "转专业申请条件",
                top_k=20,
                time_filter=TimeFilterBody(
                    mode="range", date_from="2025-09-01", date_to="2026-01-31"
                ),
            ),
            store,
            as_of=AS_OF,
        )
        ids = _ids(resp)
        assert "jwb-jxtz-2025-29001" in ids, f"区间模式未召回历史版本：{ids}"
        assert "jwb-jxtz-2026-29459" not in ids, f"区间外版本不应召回：{ids}"

    def test_long_lived_regulation_never_filtered(self, store):
        """无 effective_to 的学籍管理规定必须一直可见（is_empty 分支）。"""
        resp = retrieve(
            _query("学籍管理规定 转专业次数", top_k=20, time_filter=TimeFilterBody(mode="auto")),
            store,
            as_of=AS_OF,
        )
        assert "jwb-jxtz-2024-28417" in _ids(resp), f"长期有效规定被错误过滤：{_ids(resp)}"


class TestResponseContract:
    def test_chunks_and_scores_same_length(self, store):
        """docs/api.md §6.1：chunks 与 scores 必须等长同序。"""
        resp = retrieve(_query("选课时间", top_k=5), store, as_of=AS_OF)
        assert len(resp.chunks) == len(resp.scores)

    def test_top_k_respected(self, store):
        resp = retrieve(_query("考试安排", top_k=3), store, as_of=AS_OF)
        assert len(resp.chunks) <= 3

    def test_applied_filters_auto(self, store):
        resp = retrieve(_query("选课时间", time_filter=TimeFilterBody(mode="auto")), store, as_of=AS_OF)
        assert any("effective_to" in f for f in resp.applied_filters)
        assert any("2026-11-08" in f for f in resp.applied_filters)

    def test_applied_filters_off_is_empty(self, store):
        """mode=off 时 applied_filters 必须是空数组——E 靠它区分评估口径。"""
        resp = retrieve(_query("选课时间", time_filter=TimeFilterBody(mode="off")), store, as_of=AS_OF)
        assert resp.applied_filters == []

    def test_applied_filters_range(self, store):
        resp = retrieve(
            _query(
                "转专业",
                time_filter=TimeFilterBody(
                    mode="range", date_from="2025-01-01", date_to="2025-12-31"
                ),
            ),
            store,
            as_of=AS_OF,
        )
        assert any("2025-12-31" in f for f in resp.applied_filters)

    def test_retrieve_ms_present(self, store):
        resp = retrieve(_query("考试"), store, as_of=AS_OF)
        assert resp.retrieve_ms >= 0

    def test_no_hit_returns_empty_chunks(self, store):
        """完全不相关的查询应该几乎没有命中——由 C 侧据此拒答。"""
        resp = retrieve(_query("zzzzz qqqqq xxxxx"), store, as_of=AS_OF)
        assert len(resp.chunks) == len(resp.scores)

    def test_rerank_false_still_works(self, store):
        """rerank=false 时 scores 退化为 RRF 分，链路不能断。"""
        resp = retrieve(_query("转专业", rerank=False), store, as_of=AS_OF)
        assert len(resp.chunks) == len(resp.scores)
        assert all(0.0 <= s <= 1.0 for s in resp.scores)


class TestRRF:
    def _mk(self, chunk_id: str, dense: float = 0.0, sparse: float = 0.0) -> Scored:
        return Scored(
            chunk=Chunk(chunk_id=chunk_id, doc_id=chunk_id, title="t", text="x"),
            dense_score=dense,
            sparse_score=sparse,
        )

    def test_both_lists_bonus(self):
        dense = [self._mk("a"), self._mk("b")]
        sparse = [self._mk("a"), self._mk("c")]
        fused = rrf_fuse(dense, sparse, k=2)
        assert fused[0].scored.chunk.chunk_id == "a", "两路都命中的应排第一"

    def test_single_list_survives(self):
        fused = rrf_fuse([self._mk("a")], [self._mk("b")], k=2)
        assert {c.scored.chunk.chunk_id for c in fused} == {"a", "b"}

    def test_rank_decays(self):
        fused = rrf_fuse([self._mk("a"), self._mk("b"), self._mk("c")], [], k=2)
        scores = [c.rrf for c in fused]
        assert scores == sorted(scores, reverse=True)
