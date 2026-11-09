"""`QdrantStore` 的真实接入测试。

用 `QdrantClient(location=":memory:")` 而不是 mock：本项目的核心行为是
"`effective_to IS NULL` 的长期有效文件永远可见"，只有真引擎才能证明
`IsEmptyCondition` 确实兜住了这条。mock 只能证明"我调了我想调的东西"。

离线可跑，CI 不需要 Qdrant 容器。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from qdrant_client import QdrantClient, models

from app.embedder import DENSE_DIM, DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from app.settings import settings
from app.store import QdrantStore, build_qdrant_filter
from app.time_filter import current_payload_filter, range_payload_filter

AS_OF = "2026-11-08"


def _date(raw: str) -> date:
    return date.fromisoformat(raw)


# --------------------------------------------------------------------------- 过滤翻译


class TestBuildQdrantFilter:
    def test_none_stays_none(self):
        assert build_qdrant_filter(None) is None
        assert build_qdrant_filter({}) is None

    def test_current_filter_uses_datetime_range(self):
        """auto 模式的硬过滤：日期边界必须是 DatetimeRange，不是数值 Range。"""
        flt = build_qdrant_filter(current_payload_filter(_date(AS_OF)))

        assert flt.must is not None and len(flt.must) == 1
        cond = flt.must[0]
        assert cond.key == "effective_from"
        assert isinstance(cond.range, models.DatetimeRange), "日期过滤必须用 DatetimeRange"
        assert cond.range.lte is not None

        # should 里必须同时有 range 与 is_empty 两种条件 —— 后者是长期有效的唯一保障
        assert flt.should is not None and len(flt.should) == 2
        kinds = {type(c).__name__ for c in flt.should}
        assert kinds == {"FieldCondition", "IsEmptyCondition"}
        empty = next(c for c in flt.should if isinstance(c, models.IsEmptyCondition))
        assert empty.is_empty.key == "effective_to"

    def test_range_filter_overlaps_window(self):
        flt = build_qdrant_filter(range_payload_filter((_date("2025-01-01"), _date("2025-12-31"))))

        assert flt.must is not None
        assert flt.must[0].range.lte is not None
        assert flt.should is not None and len(flt.should) == 2

    def test_numeric_range_stays_numeric(self):
        """非日期字段（例如 doc_version_rank）不能被错当成日期。"""
        flt = build_qdrant_filter({"must": [{"key": "doc_version_rank", "range": {"gte": 202601}}]})

        assert flt.must is not None
        assert isinstance(flt.must[0].range, models.Range)
        assert not isinstance(flt.must[0].range, models.DatetimeRange)

    def test_must_not_supported(self):
        flt = build_qdrant_filter({"must_not": [{"is_empty": {"key": "effective_to"}}]})

        assert flt.must_not is not None and len(flt.must_not) == 1
        assert isinstance(flt.must_not[0], models.IsEmptyCondition)

    def test_unknown_shape_is_ignored_not_crashing(self):
        flt = build_qdrant_filter({"must": [{"nonsense": 1}]})
        assert flt.must is None
        assert flt.should is None


# ------------------------------------------------------------------------ 真实 Qdrant


@pytest.fixture()
def qdrant() -> QdrantClient:
    """内存版 Qdrant，建与 infra/scripts/init-qdrant.sh 同构的 collection。"""
    client = QdrantClient(location=":memory:")
    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config={
            DENSE_VECTOR_NAME: models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={SPARSE_VECTOR_NAME: models.SparseVectorParams()},
    )
    return client


def _payload(chunk_id: str, *, effective_from: str | None, effective_to: str | None) -> dict:
    """一条完整的 13 字段 payload，外加两个契约外的辅助字段（验证白名单容错）。"""
    return {
        "chunk_id": chunk_id,
        "doc_id": chunk_id.split("#")[0],
        "title": "关于转专业工作的通知",
        "text": "报名截止时间为4月10日17:00。",
        "publish_date": effective_from or "2024-01-01",
        "effective_from": effective_from,
        "effective_to": effective_to,
        "doc_version": "v2026.1",
        "section_path": "正文 > 一、报名安排",
        "content_type": "notice_body",
        "source_url": "https://www.sgu.edu.cn/jwb/info/1050/29459.htm",
        "is_current": True,
        "supersedes_doc_id": None,
        # 契约外字段：A 侧写入用于过滤/去重，B 必须忽略而不是报错
        "doc_version_rank": 202601,
        "content_hash": "0" * 64,
    }


def _dense(first: float) -> list[float]:
    vec = [0.0] * DENSE_DIM
    vec[0] = first
    vec[1] = max(0.0, 1.0 - first)
    return vec


def _dense_orthogonal() -> list[float]:
    """与任何 `_dense(x)` 正交，用来构造"只在 sparse 一路命中"的样本。"""
    vec = [0.0] * DENSE_DIM
    vec[2] = 1.0
    return vec


@pytest.fixture()
def seeded(qdrant: QdrantClient) -> QdrantClient:
    """三条数据：长期有效 / 已过期 / 尚未生效。"""
    qdrant.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            models.PointStruct(
                id=1,
                vector={
                    DENSE_VECTOR_NAME: _dense(1.0),
                    SPARSE_VECTOR_NAME: models.SparseVector(indices=[101, 202], values=[1.0, 0.5]),
                },
                payload=_payload(
                    "jwb-jxtz-2024-28417#c001",
                    effective_from="2024-01-01",
                    effective_to=None,
                ),
            ),
            models.PointStruct(
                id=2,
                vector={
                    DENSE_VECTOR_NAME: _dense(0.9),
                    SPARSE_VECTOR_NAME: models.SparseVector(indices=[101], values=[0.9]),
                },
                payload=_payload(
                    "jwb-jxtz-2025-29001#c001",
                    effective_from="2025-01-01",
                    effective_to="2025-05-31",
                ),
            ),
            models.PointStruct(
                id=3,
                vector={
                    DENSE_VECTOR_NAME: _dense_orthogonal(),
                    SPARSE_VECTOR_NAME: models.SparseVector(indices=[303], values=[0.8]),
                },
                payload=_payload(
                    "jwb-jxtz-2026-29701#c001",
                    effective_from="2026-12-01",
                    effective_to=None,
                ),
            ),
        ],
    )
    return qdrant


class TestQdrantStoreFiltering:
    def test_long_lived_survives_current_filter(self, seeded: QdrantClient):
        """核心行为：effective_to IS NULL 的学籍管理规定不能被 range 过滤误杀。"""
        store = QdrantStore(client=seeded)
        hits = store.hybrid_search(
            dense=_dense(1.0),
            sparse=([101], [1.0]),
            payload_filter=current_payload_filter(_date(AS_OF)),
            limit=10,
        )

        ids = {h.chunk.chunk_id for h in hits}
        assert "jwb-jxtz-2024-28417#c001" in ids, f"长期有效文档被过滤：{ids}"
        assert "jwb-jxtz-2025-29001#c001" not in ids, f"过期文档未被过滤：{ids}"
        assert "jwb-jxtz-2026-29701#c001" not in ids, f"未生效文档未被过滤：{ids}"

    def test_no_filter_returns_everything(self, seeded: QdrantClient):
        """mode=off 时 payload_filter 为 None，三条都该看见（E 的对照实验口径）。"""
        store = QdrantStore(client=seeded)
        hits = store.hybrid_search(
            dense=_dense(1.0), sparse=([101], [1.0]), payload_filter=None, limit=10
        )
        assert len(hits) == 3

    def test_both_routes_carry_scores(self, seeded: QdrantClient):
        """dense 与 sparse 两路都要被写入同一 Scored，RRF 才有的融合。"""
        store = QdrantStore(client=seeded)
        hits = store.hybrid_search(
            dense=_dense(1.0), sparse=([101, 303], [1.0, 1.0]), payload_filter=None, limit=10
        )

        by_id = {h.chunk.chunk_id: h for h in hits}
        long_lived = by_id["jwb-jxtz-2024-28417#c001"]
        assert long_lived.dense_score > 0
        assert long_lived.sparse_score > 0

        # 只在 sparse 里命中的那条（indices=[303]）也要出现，dense_score 为 0
        future = by_id["jwb-jxtz-2026-29701#c001"]
        assert future.dense_score == 0.0
        assert future.sparse_score > 0

    def test_payload_maps_to_contract_chunk(self, seeded: QdrantClient):
        """payload → Chunk：只取 13 个契约字段，契约外字段被忽略。"""
        store = QdrantStore(client=seeded)
        hits = store.hybrid_search(
            dense=_dense(1.0), sparse=([], []), payload_filter=None, limit=10
        )
        ch = hits[0].chunk
        assert set(ch.model_dump()) == {
            "chunk_id",
            "doc_id",
            "title",
            "text",
            "publish_date",
            "effective_from",
            "effective_to",
            "doc_version",
            "section_path",
            "content_type",
            "source_url",
            "is_current",
            "supersedes_doc_id",
        }
        assert ch.effective_to is None
        assert ch.source_url.startswith("https://www.sgu.edu.cn/")

    def test_ping_true_on_live_collection(self, seeded: QdrantClient):
        assert QdrantStore(client=seeded).ping() is True


class _BrokenClient:
    """两路都炸的假 client：用来确认故障不会被伪装成"没有命中"。"""

    def query_points(self, **_kw: Any) -> Any:
        raise RuntimeError("qdrant is down")

    def get_collection(self, _name: str) -> Any:
        raise RuntimeError("qdrant is down")


class TestFailureIsNotNoHit:
    def test_both_routes_failing_raises(self):
        from app.store import VectorStoreUnavailable

        with pytest.raises(VectorStoreUnavailable):
            QdrantStore(client=_BrokenClient()).hybrid_search(
                dense=_dense(1.0), sparse=([1], [1.0]), payload_filter=None, limit=10
            )

    def test_ping_false_on_broken_client(self):
        assert QdrantStore(client=_BrokenClient()).ping() is False
