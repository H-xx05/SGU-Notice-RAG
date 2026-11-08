"""chunks.jsonl 写出 + Qdrant payload 展平：契约字段的唯一装配点。

**这里是 `effective_period` 对象 → `effective_from`/`effective_to` 平铺的转换处**，
也是 SKILL.md §4 与 references/data-model.md 里"唯一字段形态差异"所指的那一步。
除本文件外，任何地方都不允许拼 chunk 的 JSON。

两种形态，一份真相：
- `chunks.jsonl`（本文件 `ChunkRecord.as_dict()`）：按 SKILL.md §4 的 schema，
  时效用 **对象** `effective_period: {from, to, precision}`，共 17 个字段
  （与 docs/data-model.md §6 的示例逐字一致）；
- Qdrant payload（本文件 `ChunkRecord.to_payload()`）：把对象**展平**成
  `effective_from` / `effective_to` 两个顶层可比较字段，精度另存
  `effective_precision`。后者不在 jsonl 契约里，只供 A 自己排查用
  （B 侧读端白名单会忽略它）。

`topic_key` 是 **notice 级** 概念（docs/data-model.md §2.3），**不进 chunk 行**；
需要时用 `doc_registry.topic_key_of(title)` 重算，结果确定可复现。

写出时的三条不变量（有测试守）：
- 每行都能被 `json.loads`，键集合 = `CONTRACT_FIELDS`；
- `chunk_id` = `{doc_id}#c{序号3位}`，同一次运行内唯一；
- `content_hash` 相同的 chunk 不重复写（幂等重跑）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

# chunks.jsonl 的契约字段（SKILL.md §4 表）。顺序即写出顺序，方便 diff。
CONTRACT_FIELDS = (
    "schema_version",
    "chunk_id",
    "doc_id",
    "title",
    "publish_date",
    "effective_period",  # 对象：{from, to, precision}
    "doc_version",
    "doc_version_rank",
    "section_path",
    "content_type",
    "source_url",
    "text",
    "token_count",
    "content_hash",
    "supersedes_doc_id",
    "is_current",
    "ingested_at",
)

PRECISIONS = ("day", "month", "term", "unknown")
CONTENT_TYPES = ("notice_body", "attachment", "table", "appendix")


@dataclass
class ChunkRecord:
    chunk_id: str
    doc_id: str
    title: str
    publish_date: str
    effective_period: dict[str, Any]  # {"from":…, "to":…, "precision":…}
    doc_version: str
    doc_version_rank: int
    section_path: str
    content_type: str
    source_url: str
    text: str
    token_count: int
    content_hash: str
    supersedes_doc_id: str | None
    is_current: bool
    ingested_at: str
    schema_version: str = SCHEMA_VERSION

    # ---------------------------------------------------------------- 形态

    def as_dict(self) -> dict[str, Any]:
        """chunks.jsonl 行：`effective_period` 保持对象。"""
        d = {
            "schema_version": self.schema_version,
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "title": self.title,
            "publish_date": self.publish_date,
            "effective_period": {
                "from": self.effective_period.get("from"),
                "to": self.effective_period.get("to"),
                "precision": self.effective_period.get("precision", "unknown"),
            },
            "doc_version": self.doc_version,
            "doc_version_rank": self.doc_version_rank,
            "section_path": self.section_path,
            "content_type": self.content_type,
            "source_url": self.source_url,
            "text": self.text,
            "token_count": self.token_count,
            "content_hash": self.content_hash,
            "supersedes_doc_id": self.supersedes_doc_id,
            "is_current": self.is_current,
            "ingested_at": self.ingested_at,
        }
        return {k: d[k] for k in CONTRACT_FIELDS}

    def to_payload(self) -> dict[str, Any]:
        """Qdrant payload：`effective_period` 展平。

        **为什么展平**：Qdrant 的 range filter 只能作用于顶层数值/日期字符串字段，
        嵌在对象里的 `from`/`to` 无法参与过滤。展平后
        `{"must": [{"key": "effective_from", "range": {"lte": as_of}}]}` 才成立。
        """
        d = self.as_dict()
        period = d.pop("effective_period")
        d["effective_from"] = period.get("from")
        d["effective_to"] = period.get("to")
        d["effective_precision"] = period.get("precision", "unknown")
        return d


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_id_of(doc_id: str, seq: int) -> str:
    return f"{doc_id}#c{seq:03d}"


@dataclass
class ChunkWriter:
    """累积 → 去重 → 落盘。落盘是**追加**语义：已存在同 chunk_id/hash 的行会被跳过。"""

    path: Path
    _seen_ids: set[str] = field(default_factory=set)
    _seen_hashes: set[str] = field(default_factory=set)
    written: int = 0
    skipped: int = 0

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = d.get("chunk_id")
                h = d.get("content_hash")
                if cid:
                    self._seen_ids.add(cid)
                if h:
                    self._seen_hashes.add(h)

    def add(self, rec: ChunkRecord) -> bool:
        """写入一条。返回 False 表示被幂等规则跳过。"""
        if rec.chunk_id in self._seen_ids or rec.content_hash in self._seen_hashes:
            self.skipped += 1
            return False
        self._seen_ids.add(rec.chunk_id)
        self._seen_hashes.add(rec.content_hash)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec.as_dict(), ensure_ascii=False) + "\n")
        self.written += 1
        return True

    def add_many(self, recs: Iterable[ChunkRecord]) -> int:
        return sum(1 for r in recs if self.add(r))


PAYLOAD_SHAPE_SENTINEL = "__payload_shape__"


def is_payload_shape(rows: list[dict[str, object]]) -> bool:
    """判断一批记录是不是 **Qdrant payload** 形态（而不是 chunks.jsonl 形态）。

    两种形态都合法，用途不同：chunks.jsonl 是落盘契约产物（带 schema_version、
    content_hash、ingested_at），payload 是写进 Qdrant 的展平版本。校验工具要能
    分辨，否则会把"拿错文件"报成"字段缺失"。
    """
    if not rows:
        return False
    has_flat = any("effective_from" in r or "effective_to" in r for r in rows)
    has_nested = any(isinstance(r.get("effective_period"), dict) for r in rows)
    return has_flat and not has_nested


def period_of(record: dict[str, object]) -> dict[str, object]:
    """把一行记录里的生效区间归一成 `{"from","to","precision"}`。

    **同一个概念在仓库里有两种合法形态**，这不是历史包袱而是刻意的：

    - `chunks.jsonl`（A 的产物、契约字段）用嵌套对象 `effective_period: {from,to,precision}`，
      因为它是文档级的语义数据，嵌套更好读，也便于校验；
    - Qdrant payload 用扁平的 `effective_from` / `effective_to`，
      因为 Qdrant 的 range filter 只能作用于顶层字段——嵌套对象过滤不了。

    转换点只有一处：`ChunkRecord.to_payload()`。但**读端**（统计、校验、排障脚本）
    会同时遇到两种形态，所以提供这个归一键，避免每个读端各写一份判断逻辑。
    """
    period = record.get("effective_period")
    if isinstance(period, dict):
        return {
            "from": period.get("from"),
            "to": period.get("to"),
            "precision": period.get("precision", "unknown"),
        }
    return {
        "from": record.get("effective_from"),
        "to": record.get("effective_to"),
        "precision": record.get("effective_precision", "unknown"),
    }


def read_chunks(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def to_payloads(path: str | Path) -> list[dict[str, Any]]:
    """把整份 chunks.jsonl 转成 Qdrant payload 列表（供 `upsert` 脚本直接用）。"""
    out = []
    for d in read_chunks(path):
        period = d.pop("effective_period", {}) or {}
        d["effective_from"] = period.get("from")
        d["effective_to"] = period.get("to")
        d["effective_precision"] = period.get("precision", "unknown")
        out.append(d)
    return out


def validate_record(d: dict[str, Any]) -> list[str]:
    """校验一行是否符合契约。返回问题列表（空 = 通过）。测试与 CLI `--check` 复用。"""
    problems: list[str] = []

    extra = set(d) - set(CONTRACT_FIELDS)
    missing = set(CONTRACT_FIELDS) - set(d)
    if extra:
        problems.append(f"多余字段：{sorted(extra)}")
    if missing:
        problems.append(f"缺失字段：{sorted(missing)}")

    cid = d.get("chunk_id", "")
    doc_id = d.get("doc_id", "")
    if not cid.startswith(doc_id + "#c"):
        problems.append(f"chunk_id 前缀不符：{cid!r} 不以 {doc_id + '#c'!r} 开头")

    if d.get("content_type") not in CONTENT_TYPES:
        problems.append(f"content_type 非法：{d.get('content_type')!r}")

    period = d.get("effective_period")
    if not isinstance(period, dict):
        problems.append(f"effective_period 不是对象：{period!r}")
    else:
        if set(period) != {"from", "to", "precision"}:
            problems.append(f"effective_period 键集合不符：{sorted(period)}")
        if period.get("precision") not in PRECISIONS:
            problems.append(f"precision 非法：{period.get('precision')!r}")
        ef, et = period.get("from"), period.get("to")
        for name, v in (("from", ef), ("to", et)):
            if v is not None and not _is_date(v):
                problems.append(f"effective_period.{name} 非 YYYY-MM-DD：{v!r}")
        if ef and et and ef > et:
            problems.append(f"effective_period.from({ef}) 晚于 to({et})")

    if d.get("is_current") not in (True, False):
        problems.append(f"is_current 非布尔：{d.get('is_current')!r}")

    if not isinstance(d.get("token_count"), int) or d.get("token_count", 0) <= 0:
        problems.append(f"token_count 非正整数：{d.get('token_count')!r}")

    h = d.get("content_hash", "")
    if not (isinstance(h, str) and len(h) == 64):
        problems.append(f"content_hash 非 sha256：{h!r}")

    return problems


def _is_date(v: Any) -> bool:
    if not isinstance(v, str) or len(v) != 10:
        return False
    return v[4] == "-" and v[7] == "-" and v.replace("-", "").isdigit()
