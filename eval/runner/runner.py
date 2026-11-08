"""跑一批 query，收集观测。走 HTTP，不 import 别人的代码。

为什么走 HTTP 而不是直接调 B 的 retrieve()：
  - 评估对象是**部署后的系统**，不是某个函数。绕过 HTTP 就测不出超时、序列化、
    限流这些真实会发生的问题。
  - 走 HTTP 才能顺带验证 C↔B 的契约，一箭双雕。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .metrics import QueryOutcome

DEFAULT_TIMEOUT = 30.0


@dataclass
class Endpoint:
    """被测系统。默认打到 Go 的 /api/v1/chat（端到端），可改指 B 的 /internal/retrieve。"""

    url: str
    token: str | None = None
    timeout: float = DEFAULT_TIMEOUT


class RunnerError(RuntimeError):
    pass


def ask(endpoint: Endpoint, query: str, as_of: str, top_k: int = 8) -> dict[str, Any]:
    """发一条问句，返回 C 的 data 段。"""
    body = {
        "query": query,
        "top_k": top_k,
        "time_filter": {"mode": "auto", "date_from": None, "date_to": None, "as_of": as_of},
    }
    req = urllib.request.Request(
        endpoint.url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **({"Authorization": f"Bearer {endpoint.token}"} if endpoint.token else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=endpoint.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RunnerError(f"HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RunnerError(f"连不上 {endpoint.url}：{exc.reason}") from exc

    if payload.get("code") != 0:
        raise RunnerError(f"业务错误 {payload.get('code')}：{payload.get('message')}")
    return payload["data"]


def observe(endpoint: Endpoint, outcome: QueryOutcome, query: str) -> QueryOutcome:
    """对一条 query 采集观测，写回 outcome。失败时保留空观测并记 refused=False。

    单条失败不中断整批 —— 40 条跑了 30 条炸掉，比跑完拿到 39 条好。
    """
    started = time.monotonic()
    try:
        data = ask(endpoint, query, outcome.as_of)
    except RunnerError:
        outcome.retrieved_doc_ids = []
        outcome.retrieved_buckets = []
        outcome.refused = False
        return outcome

    outcome.answer = data.get("answer", "")
    outcome.refused = bool(data.get("refused"))
    outcome.refuse_reason = data.get("refuse_reason")
    citations = data.get("citations") or []
    outcome.retrieved_doc_ids = [c.get("doc_id", "") for c in citations]
    outcome.retrieved_buckets = [c.get("recency_bucket", "unknown") for c in citations]
    _ = time.monotonic() - started
    return outcome


def run_batch(endpoint: Endpoint, rows: list[dict]) -> list[QueryOutcome]:
    """顺序跑，不并发。

    评估不需要快，需要**可复现**。并发会让限流（30/min）触发，
    失败率混进指标里，你还以为是模型退化。
    """
    out: list[QueryOutcome] = []
    from .dataset import to_outcomes

    for outcome, row in zip(to_outcomes(rows), rows, strict=True):
        out.append(observe(endpoint, outcome, row["query"]))
    return out
