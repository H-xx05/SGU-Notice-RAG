"""向量库访问层：Qdrant 的薄封装 + 内存兜底。

为什么需要内存兜底：B 的单元测试和 CI 不能依赖 Qdrant 容器。`FixtureStore` 从
`tests/fixtures/chunks.jsonl` 读同样的字段形态，于是"过滤→融合→精排→降权→拒答"
这条链路在两种 store 上是同一份代码，测出来的行为就是线上的行为。

需要替换向量库时（见 references/tech-baseline.md 的允许替换表），只要保持
`hybrid_search()` 的入参/出参语义，retriever.py 不用动。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .embedder import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from .schemas import Chunk
from .settings import settings

log = logging.getLogger(__name__)


class VectorStoreUnavailable(RuntimeError):
    """向量库两路召回都失败。

    必须抛出来，不能返回空列表：空列表在 C 侧等于 `no_hit` → 拒答话术"未找到依据"。
    把"向量库挂了"伪装成"没有这条通知"，正是本项目要消灭的那类幻觉——
    只不过这次骗的是运维，不是用户。
    """


@dataclass
class Scored:
    """一条召回结果：chunk + 两路分数。"""

    chunk: Chunk
    dense_score: float = 0.0
    sparse_score: float = 0.0


class VectorStore(Protocol):
    def hybrid_search(
        self,
        *,
        dense: list[float],
        sparse: tuple[list[int], list[float]],
        payload_filter: dict | None,
        limit: int,
    ) -> list[Scored]: ...

    def ping(self) -> bool: ...


# --------------------------------------------------------------------------- Qdrant

# payload_filter 里的日期边界形如 `2026-11-08`（也兼容带时间的 RFC3339）。
# Qdrant 的 range 分两种模型：`Range` 只吃数值，日期必须用 `DatetimeRange`。
# 骨架期用 `models.Filter(**payload_filter)` 直接塞 dict，pydantic 会"猜"成 DatetimeRange，
# 但只要字段名或取值形态稍有偏差（例如数值型 doc_version_rank），就会静默选错类型。
# 因此这里显式按取值形态分派，规则可读、可测，不依赖 union 的匹配顺序。
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _is_date_bound(rng: dict[str, Any]) -> bool:
    return any(isinstance(v, str) and _ISO_DATE_RE.match(v) for v in rng.values())


def _condition(models: Any, cond: dict[str, Any]) -> Any | None:
    """把 time_filter 产出的条件 dict 翻译成 Qdrant 的 condition 对象。

    只支持本项目实际用到的三种形态，其余返回 None（忽略而不是报错）：
      `{"key": K, "range": {...}}`   区间，日期走 DatetimeRange、数值走 Range
      `{"is_empty": {"key": K}}`     K 为 null 或缺失（长期有效）
    """
    if "is_empty" in cond:
        key = (cond["is_empty"] or {}).get("key")
        if not key:
            return None
        return models.IsEmptyCondition(is_empty=models.PayloadField(key=key))

    key = cond.get("key")
    if not key:
        return None

    if "range" in cond:
        rng = {k: v for k, v in (cond["range"] or {}).items() if v is not None}
        if not rng:
            return None
        if _is_date_bound(rng):
            return models.FieldCondition(key=key, range=models.DatetimeRange(**rng))
        return models.FieldCondition(
            key=key, range=models.Range(**{k: float(v) for k, v in rng.items()})
        )

    if "match" in cond:
        return models.FieldCondition(key=key, match=models.MatchValue(**(cond["match"] or {})))

    return None


def build_qdrant_filter(payload_filter: dict | None) -> Any | None:
    """dict 形态的 payload filter → `models.Filter`。

    保留 `must` / `should` / `must_not` 三组语义（`should` 在本项目里专门用来兜住
    "effective_to IS NULL"，删掉或降级成 `must` 都会让长期有效的规定整条消失）。
    """
    if not payload_filter:
        return None

    from qdrant_client import models

    def translate(name: str) -> list[Any] | None:
        raw = payload_filter.get(name) or []
        out = [c for c in (_condition(models, cond) for cond in raw) if c is not None]
        if len(out) != len(raw):
            log.warning("payload_filter.%s 有 %d 条条件无法翻译，已忽略", name, len(raw) - len(out))
        return out or None

    return models.Filter(
        must=translate("must"),
        should=translate("should"),
        must_not=translate("must_not"),
    )


class QdrantStore:
    """真实向量库。检索分两路（dense / sparse），各自取 topN，融合在 retriever 里做。

    用 `query_points` 而不是 `search`：`search` 在 qdrant-client 1.13 起标废弃、
    1.15 移除。项目锁 1.12.1，两个都能用，但选不会在下一次升级时爆炸的那个。
    """

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            from qdrant_client import QdrantClient

            client = QdrantClient(
                url=settings.vector_db_url,
                api_key=settings.qdrant_api_key or None,
                timeout=10,
            )
        # client 可注入：测试用 `QdrantClient(location=":memory:")` 跑真实过滤语义，
        # 既不需要容器也不需要网络，还比 mock 更能证明"is_empty 分支真的生效"。
        self._client = client
        self._collection = settings.qdrant_collection

    def ping(self) -> bool:
        try:
            return bool(self._client.get_collection(self._collection))
        except Exception:  # noqa: BLE001 - 健康检查不应该抛
            log.warning("qdrant ping failed", exc_info=True)
            return False

    def _query(
        self,
        *,
        vector: Any,
        using: str,
        payload_filter: Any | None,
        limit: int,
    ) -> list[Any]:
        resp = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            using=using,
            query_filter=payload_filter,
            limit=limit,
            with_payload=True,
        )
        return list(resp.points)

    def hybrid_search(
        self,
        *,
        dense: list[float],
        sparse: tuple[list[int], list[float]],
        payload_filter: dict | None,
        limit: int,
    ) -> list[Scored]:
        from qdrant_client import models

        flt = build_qdrant_filter(payload_filter)
        out: dict[str, Scored] = {}
        failures: list[str] = []

        try:
            dense_hits = self._query(
                vector=dense, using=DENSE_VECTOR_NAME, payload_filter=flt, limit=limit
            )
        except Exception:  # noqa: BLE001
            log.warning("dense search failed", exc_info=True)
            dense_hits = []
            failures.append("dense")

        for h in dense_hits:
            ch = _chunk_from_payload(h.payload or {})
            out[ch.chunk_id] = Scored(chunk=ch, dense_score=float(h.score))

        indices, values = sparse
        if indices:
            try:
                sparse_hits = self._query(
                    vector=models.SparseVector(indices=indices, values=values),
                    using=SPARSE_VECTOR_NAME,
                    payload_filter=flt,
                    limit=limit,
                )
            except Exception:  # noqa: BLE001
                log.warning("sparse search failed", exc_info=True)
                sparse_hits = []
                failures.append("sparse")

            for h in sparse_hits:
                ch = _chunk_from_payload(h.payload or {})
                if ch.chunk_id in out:
                    out[ch.chunk_id].sparse_score = float(h.score)
                else:
                    out[ch.chunk_id] = Scored(chunk=ch, sparse_score=float(h.score))

        if len(failures) == 2:
            raise VectorStoreUnavailable(
                f"qdrant 两路召回均失败（collection={self._collection}）: {', '.join(failures)}"
            )

        return list(out.values())


def _chunk_from_payload(p: dict[str, Any]) -> Chunk:
    """Qdrant payload -> Chunk。

    白名单映射 + 容错：payload 里允许存在契约外的辅助字段（`doc_version_rank`、
    `topic_key`、`content_hash`、`ingested_at` 等，A 侧写入时用于过滤与去重），
    这里只取契约字段，其余一律忽略——向前兼容，新增字段不会打挂 B。
    `url` 是 A 侧的旧字段名，映射到契约名 `source_url`。
    """
    return Chunk(
        chunk_id=str(p.get("chunk_id", "")),
        doc_id=str(p.get("doc_id", "")),
        title=str(p.get("title", "")),
        text=str(p.get("text", "")),
        publish_date=str(p.get("publish_date", "") or ""),
        effective_from=_opt_str(p.get("effective_from")),
        effective_to=_opt_str(p.get("effective_to")),
        doc_version=str(p.get("doc_version", "") or ""),
        section_path=str(p.get("section_path", "") or ""),
        content_type=str(p.get("content_type", "") or "notice_body"),
        source_url=str(p.get("source_url") or p.get("url") or ""),
        is_current=bool(p.get("is_current", True)),
        supersedes_doc_id=_opt_str(p.get("supersedes_doc_id")),
    )


def _opt_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# ------------------------------------------------------------------ Fixture / 内存兜底


@dataclass
class FixtureStore:
    """从 jsonl 读固定数据，用与线上同构的 payload_filter 语义做内存过滤。

    仅用于测试与 `RAG_STUB_MODE=true` 的本地联调；不追求性能。
    """

    items: list[Scored] = field(default_factory=list)

    @classmethod
    def from_jsonl(cls, path: str | Path) -> FixtureStore:
        p = Path(path)
        items: list[Scored] = []
        if not p.exists():
            return cls(items=items)
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            items.append(Scored(chunk=_chunk_from_payload(payload)))
        return cls(items=items)

    def ping(self) -> bool:
        return True

    def hybrid_search(
        self,
        *,
        dense: list[float],
        sparse: tuple[list[int], list[float]],
        payload_filter: dict | None,
        limit: int,
    ) -> list[Scored]:
        from .embedder import dense_embed, sparse_embed  # 延迟导入避免循环

        kept = [it for it in self.items if _passes(it.chunk, payload_filter)]

        scored: list[Scored] = []
        for it in kept:
            d = _cosine(dense, dense_embed(it.chunk.text))
            _idx, sval = sparse_embed(it.chunk.text)
            s = sum(sval) / (len(sval) or 1)
            scored.append(Scored(chunk=it.chunk, dense_score=d, sparse_score=s))

        scored.sort(key=lambda x: (x.dense_score, x.sparse_score), reverse=True)
        return scored[:limit]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math_sqrt(sum(v * v for v in a[:n]))
    nb = math_sqrt(sum(v * v for v in b[:n]))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def math_sqrt(x: float) -> float:
    return x**0.5


def _passes(ch: Chunk, payload_filter: dict | None) -> bool:
    """内存版 payload filter：实现本系统用到的三种条件（range / is_empty / should）。

    与 Qdrant 语义对齐：
      must   全部满足
      should 至少满足一条（本系统只用它表达"长期有效"这一分支）
    """
    if not payload_filter:
        return True

    if not all(_match_cond(ch, c) for c in payload_filter.get("must", [])):
        return False

    should = payload_filter.get("should") or []
    if should and not any(_match_cond(ch, c) for c in should):
        return False

    return True


def _match_cond(ch: Chunk, cond: dict) -> bool:
    if "is_empty" in cond:
        key = cond["is_empty"].get("key")
        if key == "effective_to":
            return ch.effective_to in (None, "")
        if key == "effective_from":
            return ch.effective_from in (None, "")
        return False

    key = cond.get("key")
    rng = cond.get("range") or {}
    if key == "effective_from":
        val = ch.effective_from
    elif key == "effective_to":
        val = ch.effective_to
    else:
        return True

    if "lte" in rng:
        if val is None:
            return key == "effective_from"  # 无起点视为 -inf，满足 <=
        if str(val) > str(rng["lte"]):
            return False
    if "gte" in rng:
        if val is None:
            return key == "effective_to"  # 无终点视为 +inf，满足 >=
        if str(val) < str(rng["gte"]):
            return False
    return True


def build_store() -> VectorStore:
    """按配置选 store；Qdrant 不可用时自动降级到 fixture（仅 stub 模式允许）。"""
    if settings.rag_stub_mode:
        fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "chunks.jsonl"
        if fixture.exists():
            log.info("stub mode: using fixture store (%s)", fixture)
            return FixtureStore.from_jsonl(fixture)
        log.info("stub mode: no fixture, using live qdrant")
    return QdrantStore()
