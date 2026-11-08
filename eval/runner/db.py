"""把评估结果写进 Postgres，供 C 的 /api/v1/eval/summary 读取。

表结构见 docs/data-model.md 的 eval_runs / eval_results。
用 psycopg（同步）而不是 asyncpg：评估是离线批处理，不需要 async。

没装 psycopg 或没配 DSN 时**不报错**，只打印一行提示 —— 本地跑评估不该被迫起数据库。
"""

from __future__ import annotations

import json
import os
from typing import Any

from .metrics import QueryOutcome


class DbUnavailable(RuntimeError):
    pass


def write_run(summary: dict[str, Any], outcomes: list[QueryOutcome]) -> str:
    # POSTGRES_URL 是 docker-compose / docs/deployment.md 里的正式变量名；
    # DATABASE_DSN 是 E 组本地跑批时更顺手的短名。两个都认，正式名优先。
    dsn = (
        os.environ.get("POSTGRES_URL")
        or os.environ.get("DATABASE_DSN")
        or os.environ.get("POSTGRES_DSN")
    )
    if not dsn:
        raise DbUnavailable("未设置 DATABASE_DSN，跳过入库（只写本地报告）")

    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DbUnavailable("未安装 psycopg，跳过入库（pip install psycopg[binary]）") from exc

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO eval_runs (run_id, created_at, dataset_size, metrics, by_year, regressions)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
            ON CONFLICT (run_id) DO UPDATE SET
                created_at   = EXCLUDED.created_at,
                dataset_size = EXCLUDED.dataset_size,
                metrics      = EXCLUDED.metrics,
                by_year      = EXCLUDED.by_year,
                regressions  = EXCLUDED.regressions
            """,
            (
                summary["run_id"],
                summary["created_at"],
                summary["dataset_size"],
                json.dumps(summary["metrics"], ensure_ascii=False),
                json.dumps(summary["by_year"], ensure_ascii=False),
                json.dumps(summary["regressions"], ensure_ascii=False),
            ),
        )
        for o in outcomes:
            cur.execute(
                """
                INSERT INTO eval_results
                    (run_id, query_id, category, as_of, answer, refused,
                     stale_hit, citations, metrics)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (run_id, query_id) DO UPDATE SET
                    category  = EXCLUDED.category,
                    as_of     = EXCLUDED.as_of,
                    answer    = EXCLUDED.answer,
                    refused   = EXCLUDED.refused,
                    stale_hit = EXCLUDED.stale_hit,
                    citations = EXCLUDED.citations,
                    metrics   = EXCLUDED.metrics
                """,
                (
                    summary["run_id"],
                    o.query_id,
                    o.category,
                    o.as_of or None,
                    o.answer,
                    o.refused,
                    bool(o.stale_hits),
                    json.dumps(
                        [
                            {"doc_id": d, "recency_bucket": b}
                            for d, b in zip(
                                o.retrieved_doc_ids, o.retrieved_buckets, strict=False
                            )
                        ],
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "hit": o.hit,
                            "first_hit_rank": o.first_hit_rank,
                            "stale_hits": o.stale_hits,
                            "refuse_reason": o.refuse_reason,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        conn.commit()
    return summary["run_id"]
