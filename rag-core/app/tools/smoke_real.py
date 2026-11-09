"""真实模式冒烟：一条命令确认"模型 + 向量库"是真的接上了。

验收口径（Issue #2「本周五验收」）落到这个脚本里：

1. `GET /internal/health` → 200 且 `qdrant`/`embedder`/`reranker` 三个组件都是 `ok`；
2. `POST /internal/retrieve` → 顶层恰好 4 个键，每个 chunk 恰好 13 个契约字段，
   `chunks` 与 `scores` 等长同序；
3. 打印 top-N 结果与耗时，让"能不能用"一眼可见——分数全 0 或全 1、全来自同一条文档，
   都是接入失败的典型症状（归一化算错 / 过滤条件丢了 / payload 映射错位）。

用法：

    # 容器内（rag-core 已起）
    docker compose exec rag-core python -m app.tools.smoke_real
    # 本机
    python -m app.tools.smoke_real --base-url http://localhost:8000 --query "2026年转专业报名截止日期"

退出码：0 = 全部通过；1 = 有断言失败（不是异常，是"接上了但不对"）。
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

TOP_KEYS = {"chunks", "scores", "applied_filters", "retrieve_ms"}
CHUNK_KEYS = {
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


def _get(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - 内部服务，地址来自参数
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return resp.status, json.loads(resp.read().decode("utf-8"))


def check_health(base: str, problems: list[str]) -> None:
    print("== 1. /internal/health ==")
    try:
        status, body = _get(f"{base}/internal/health")
    except urllib.error.HTTPError as exc:  # 503 也带 JSON，要读出来看是哪个组件 down
        status, body = exc.code, json.loads(exc.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        problems.append(f"连不上 {base}：{exc.reason}")
        print(f"  ✗ 连不上 {base}：{exc.reason}")
        return

    components = body.get("components") or {}
    print(f"  status={status} status_field={body.get('status')} version={body.get('version')}")
    for name in ("qdrant", "embedder", "reranker"):
        state = components.get(name)
        mark = "✓" if state == "ok" else "✗"
        print(f"  {mark} {name}: {state}")
        if state != "ok":
            problems.append(f"组件 {name} 不是 ok（当前 {state}）")
    if status != 200:
        problems.append(f"/internal/health 返回 {status}，期望 200")


def check_retrieve(base: str, query: str, top_k: int, problems: list[str]) -> None:
    print(f"== 2. /internal/retrieve （query={query!r}）==")
    body = {"query": query, "time_filter": {"mode": "auto"}, "top_k": top_k}
    try:
        status, data = _post(f"{base}/internal/retrieve", body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        problems.append(f"/internal/retrieve 返回 {exc.code}：{detail}")
        print(f"  ✗ HTTP {exc.code}：{detail}")
        return

    if status != 200:
        problems.append(f"/internal/retrieve 返回 {status}")
        return

    extra = set(data) - TOP_KEYS
    missing = TOP_KEYS - set(data)
    if extra or missing:
        problems.append(f"响应顶层键不符：多余 {sorted(extra)}，缺失 {sorted(missing)}")
    print(f"  ✓ 顶层键 {sorted(data)}")

    chunks = data.get("chunks") or []
    scores = data.get("scores") or []
    print(f"  candidates={len(chunks)} retrieve_ms={data.get('retrieve_ms')}")
    print(f"  applied_filters={data.get('applied_filters')}")

    if len(chunks) != len(scores):
        problems.append(f"chunks({len(chunks)}) 与 scores({len(scores)}) 不等长")

    for i, chunk in enumerate(chunks):
        bad = set(chunk) ^ CHUNK_KEYS
        if bad:
            problems.append(f"第 {i} 条 chunk 字段不符：{sorted(bad)}")
            break

    if not chunks:
        problems.append("召回为空：检查 collection 是否已灌入、向量维度是否一致")
        return

    for i, (chunk, score) in enumerate(zip(chunks, scores, strict=False), start=1):
        print(
            f"  {i}. score={score:.4f} bucket={'current' if chunk.get('is_current') else 'stale'} "
            f"{chunk.get('doc_id')} | {chunk.get('section_path')}"
        )

    if all(s == 0 for s in scores):
        problems.append("所有分数为 0：多半是 reranker 没生效或 payload 里 text 为空")
    if len({c.get("doc_id") for c in chunks}) == 1:
        print("  ! 全部命中同一条文档——不是错误，但值得看一下 query 是不是太窄")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="rag-core 真实模式冒烟")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("RAG_CORE_URL", "http://localhost:8000"),
        help="rag-core 地址（默认取 RAG_CORE_URL，再退回 localhost:8000）",
    )
    parser.add_argument("--query", default="2026年转专业报名截止日期")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    problems: list[str] = []

    check_health(base, problems)
    print()
    check_retrieve(base, args.query, args.top_k, problems)

    print()
    if problems:
        print("冒烟未通过：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("冒烟通过：模型与向量库已接通，契约形状不变。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
