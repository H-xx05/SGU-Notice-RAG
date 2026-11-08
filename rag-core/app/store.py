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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .schemas import Chunk
from .settings import settings

log = logging.getLogger(__name__)


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


class QdrantStore:
    """真实向量库。检索分两路（dense / sparse），各自取 topN，融合在 retriever 里做。"""

    def __init__(self) -> None:
        from qdrant_client import QdrantClient

        self._client = QdrantClient(
            url=settings.vector_db_url,
            api_key=settings.qdrant_api_key or None,
            timeout=10,
        )
        self._collection = settings.qdrant_collection

    def ping(self) -> bool:
        try:
            return bool(self._client.get_collection(self._collection))
        except Exception:  # noqa: BLE001 - 健康检查不应该抛
            log.warning("qdrant ping failed", exc_info=True)
            return False

    def hybrid_search(
        self,
        *,
        dense: list[float],
        sparse: tuple[list[int], list[float]],
        payload_filter: dict | None,
        limit: int,
    ) -> list[Scored]:
        from qdrant_client import models

        flt = models.Filter(**payload_filter) if payload_filter else None
        out: dict[str, Scored] = {}

        try:
            dense_hits = self._client.search(
                collection_name=self._collection,
                query_vector=models.NamedVector(name="dense", vector=dense),
                query_filter=flt,
                limit=limit,
                with_payload=True,
            )
        except Exception:  # noqa: BLE001
            log.warning("dense search failed", exc_info=True)
            dense_hits = []

        for h in dense_hits:
            ch = _chunk_from_payload(h.payload or {})
            out[ch.chunk_id] = Scored(chunk=ch, dense_score=float(h.score))

        indices, values = sparse
        if indices:
            try:
                sparse_hits = self._client.search(
                    collection_name=self._collection,
                    query_vector=models.NamedSparseVector(
                        name="sparse",
                        vector=models.SparseVector(indices=indices, values=values),
                    ),
                    query_filter=flt,
                    limit=limit,
                    with_payload=True,
                )
            except Exception:  # noqa: BLE001
                log.warning("sparse search failed", exc_info=True)
                sparse_hits = []

            for h in sparse_hits:
                ch = _chunk_from_payload(h.payload or {})
                if ch.chunk_id in out:
                    out[ch.chunk_id].sparse_score = float(h.score)
                else:
                    out[ch.chunk_id] = Scored(chunk=ch, sparse_score=float(h.score))

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
