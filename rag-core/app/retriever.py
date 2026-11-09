"""检索主链路（SKILL.md §5 第 2–7 步的实现）。

    硬过滤(payload) -> dense+sparse 各取 top40 -> RRF(k=2) 融合
      -> 精排 top20 -> 取 top8 -> 过期护栏降权 -> 返回

**顺序不可调换**，每一步都有理由：
- 硬过滤放最前：先砍掉时间上不可能有效的记录，省掉后面的精排开销，也避免"过期文档挤掉现行文档"。
- RRF 而不是加权和：dense 与 sparse 的分数尺度不同，RRF 只看名次，无需调权重、无需归一化。
- 精排放在融合之后：给 40+40 条候选做 cross-encoder 太贵，只在融合后的头部做。
- 降权放在精排之后、截断之前：先按相关性排序，再按时效打折，这样"高度相关但过期"能排在
  "中等相关但现行"之后，而不会被直接删掉——用户仍然能看到历史版本，只是不占首位。

出口严格按 docs/api.md §6.1：`chunks` 与 `scores` 等长同序，`applied_filters` 如实记录。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date

from . import time_filter
from .embedder import dense_embed, rerank, sparse_embed
from .schemas import RetrieveRequest, RetrieveResponse
from .settings import settings
from .store import Scored, VectorStore
from .timeline import parse_iso_date, today

log = logging.getLogger(__name__)


@dataclass
class _Candidate:
    scored: Scored
    rank_dense: int = 0
    rank_sparse: int = 0
    rrf: float = 0.0
    rerank: float = 0.0
    final: float = 0.0


def rrf_fuse(dense: list[Scored], sparse: list[Scored], k: int = 2) -> list[_Candidate]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)，rank 从 1 开始。

    k 取小值（默认 2）让"只在一路里排第一"也能压过"两路都排第十"——
    对通知检索这种查询来说，强命中比广覆盖更重要。
    """
    pool: dict[str, _Candidate] = {}

    for i, it in enumerate(dense, start=1):
        c = pool.setdefault(it.chunk.chunk_id, _Candidate(scored=it))
        c.rank_dense = i
        c.rrf += 1.0 / (k + i)

    for i, it in enumerate(sparse, start=1):
        c = pool.get(it.chunk.chunk_id)
        if c is None:
            c = _Candidate(scored=it)
            pool[it.chunk.chunk_id] = c
        c.rank_sparse = i
        c.rrf += 1.0 / (k + i)
        # 保留两路里更高的那路原始分，供 E 侧归因分析
        c.scored.dense_score = max(c.scored.dense_score, it.dense_score)
        c.scored.sparse_score = max(c.scored.sparse_score, it.sparse_score)

    ordered = sorted(pool.values(), key=lambda c: c.rrf, reverse=True)
    return ordered


def _build_filter(req: RetrieveRequest, ref: date) -> tuple[dict | None, list[str]]:
    """构造 payload filter，并同步产出**如实**的 applied_filters 说明。

    applied_filters 是 E 组区分评估口径的唯一依据（docs/api.md §6.1），
    因此它必须由与 filter 相同的分支产出，不能事后拼字符串。
    """
    tf = req.time_filter
    as_of = tf.as_of or ref.isoformat()

    if tf.mode == "off":
        return None, []

    window = time_filter.resolve_window(tf, ref)
    if window is not None:
        start, end = window
        return time_filter.range_payload_filter(window), [
            f"effective_from<={end.isoformat()}",
            f"effective_to>={start.isoformat()} OR effective_to IS NULL",
        ]

    return time_filter.current_payload_filter(ref), [
        f"effective_from<={as_of}",
        f"effective_to>={as_of} OR effective_to IS NULL",
    ]


def retrieve(
    req: RetrieveRequest, store: VectorStore, *, as_of: date | None = None
) -> RetrieveResponse:
    """执行一次检索。纯逻辑，HTTP 层只负责搬运。"""
    t0 = time.perf_counter()
    ref = as_of or _as_of_from(req) or today()

    payload_filter, applied = _build_filter(req, ref)

    dense_vec = dense_embed(req.query)
    sparse_vec = sparse_embed(req.query)

    # 一次调用拿两路候选（Qdrant 内部各 search 一次；fixture 用同一批候选打分）
    hits = store.hybrid_search(
        dense=dense_vec,
        sparse=sparse_vec,
        payload_filter=payload_filter,
        limit=max(settings.dense_top_k, settings.sparse_top_k),
    )

    dense_hits = sorted(hits, key=lambda h: h.dense_score, reverse=True)[: settings.dense_top_k]
    sparse_hits = sorted(hits, key=lambda h: h.sparse_score, reverse=True)[: settings.sparse_top_k]

    candidates = rrf_fuse(dense_hits, sparse_hits, k=settings.rrf_k)

    # 精排：只对融合后的头部做，控制延迟。rerank=false 时跳过（评估对照实验用）。
    if req.rerank:
        head = candidates[: settings.rerank_top_k]
        if head:
            scores = rerank(req.query, [c.scored.chunk.text for c in head])
            for c, s in zip(head, scores, strict=False):
                c.rerank = float(s)

    # 过期护栏 + 最终打分
    for c in candidates:
        ch = c.scored.chunk
        decision = time_filter.judge(
            effective_from=ch.effective_from,
            effective_to=ch.effective_to,
            publish_date=ch.publish_date,
            as_of=ref,
        )
        ch.is_current = decision.is_current

        if c.rerank:
            base = c.rerank if c.rerank > 0 else 0.0
        else:
            # 无 rerank 时用 RRF 分；RRF 值域约 (0, 1]，乘 2 拉到与 rerank 可比区间
            base = min(c.rrf * 2.0, 1.0)
        c.final = base * decision.penalty_factor

    candidates.sort(key=lambda c: c.final, reverse=True)
    top = candidates[: req.top_k]

    elapsed = int((time.perf_counter() - t0) * 1000)
    return RetrieveResponse(
        chunks=[c.scored.chunk for c in top],
        scores=[round(c.final, 6) for c in top],
        applied_filters=applied,
        retrieve_ms=elapsed,
    )


def _as_of_from(req: RetrieveRequest) -> date | None:
    if not req.time_filter.as_of:
        return None
    return parse_iso_date(req.time_filter.as_of)


def _normalise_state(state: str) -> str:
    """把状态收敛到契约枚举。非法值一律按 down —— 宁可报红，也不报假绿。"""
    return state if state in {"ok", "loading", "down"} else "down"


def build_health(
    store: VectorStore,
    version: str,
    *,
    embedder_state: str = "ok",
    reranker_state: str | None = None,
) -> tuple[dict, bool]:
    """返回 (payload, http_ok)。

    契约（docs/api.md §6.2）：任一组件不是 ok → HTTP 503 + status=degraded。

    三个状态而不是两个：`loading` 是契约 `ComponentState` 里本来就有的枚举，
    真实模式启动后要几十秒加载 2.3 GB 权重，这段时间既不是 ok 也不是 down。
    把它们混成 down 会让"刚启动"和"权重没下"看起来一样，排障的人得多猜一步。

    embedder 与 reranker 分开上报：两个模型是两次独立的加载，只报一个状态会让
    "embedder 好了、reranker 的权重没下完"这种半就绪状态显示成全绿。
    """
    qdrant_ok = store.ping()
    if reranker_state is None:
        reranker_state = embedder_state
    components: dict[str, str] = {
        "qdrant": "ok" if qdrant_ok else "down",
        "embedder": _normalise_state(embedder_state),
        "reranker": _normalise_state(reranker_state),
    }
    ok = all(v == "ok" for v in components.values())
    return {
        "status": "ok" if ok else "degraded",
        "components": components,
        "version": version,
    }, ok
