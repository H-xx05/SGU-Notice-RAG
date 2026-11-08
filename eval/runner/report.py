"""把指标写成两种东西：

1. `report/run-<id>.json`：给程序读，E 的 CI 归档、D 的看板都可以取
2. `report/run-<id>.md`：给人读，PR 里贴这个

JSON 的键名必须与 docs/api.md §5 的 data 段一致，直接用 `build_summary()`
就能当成 `/api/v1/eval/summary` 的响应体喂给前端 Mock。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .metrics import Metrics, QueryOutcome, by_year, mrr

SHANGHAI_OFFSET = "+08:00"
THRESHOLDS_PATH = Path(__file__).resolve().parents[1] / "thresholds.json"


def thresholds() -> dict:
    """读 thresholds.json。缺失时返回空 dict —— 报告还能出，只是不算达标与否。

    阈值只有一处权威来源，本文件不硬编码任何数字。
    """
    try:
        return json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _targets() -> dict[str, dict]:
    return thresholds().get("targets") or {}


def now_iso() -> str:
    """带 +08:00 的 RFC3339。用固定偏移，避免 CI 机器时区不同导致时间戳漂移。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_id(prefix: str = "run") -> str:
    return f"{prefix}-{datetime.now().astimezone().strftime('%Y%m%d-%H%M')}"


def build_summary(
    outcomes: list[QueryOutcome],
    metrics: Metrics,
    *,
    rid: str | None = None,
    created_at: str | None = None,
    previous: dict | None = None,
) -> dict:
    """构造与 docs/api.md §5 同形的 data。previous 用于算 regressions。"""
    return {
        "run_id": rid or run_id(),
        "created_at": created_at or now_iso(),
        "dataset_size": len(outcomes),
        "metrics": metrics.as_dict(),
        "by_year": by_year(outcomes),
        "regressions": _regressions(metrics, previous),
    }


def _regressions(metrics: Metrics, previous: dict | None) -> list[dict]:
    if not previous:
        return []
    prev = (previous.get("metrics") or {}) if isinstance(previous, dict) else {}
    out = []
    for name, value in metrics.as_dict().items():
        old = prev.get(name)
        if value is None or old is None:
            continue
        delta = round(value - old, 4)
        # 只有劣化才报：stale_recall_rate 上涨是坏事，其余下跌是坏事
        worse = delta > 0 if name == "stale_recall_rate" else delta < 0
        if worse and abs(delta) >= 0.01:
            out.append(
                {"metric": name, "delta": delta, "vs_run": previous.get("run_id", "unknown")}
            )
    return out


def write_json(summary: dict, out_dir: Path, outcomes: list[QueryOutcome] | None = None) -> Path:
    """写 run-<id>.json。

    **summary 的键必须与 /api/v1/eval/summary 的 data 段完全一致**，所以逐条样本
    不塞进 summary，另外写到 `<run_id>.samples.json` —— 那是给 ragas 回填用的
    中间产物，把原始答案灌进契约响应体是自找麻烦。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{summary['run_id']}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if outcomes:
        _write_samples(summary["run_id"], outcomes, out_dir)
    return path


def _write_samples(rid: str, outcomes: list[QueryOutcome], out_dir: Path) -> None:
    rows = [
        {
            "query_id": o.query_id,
            "category": o.category,
            "as_of": o.as_of,
            "answer": o.answer,
            "refused": o.refused,
            "retrieved_doc_ids": o.retrieved_doc_ids,
        }
        for o in outcomes
    ]
    (out_dir / f"{rid}.samples.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def read_samples(report_path: Path) -> list[dict]:
    """读同名的 .samples.json；没有就返回空列表（老报告没这个文件）。"""
    p = report_path.with_name(report_path.stem + ".samples.json")
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def write_markdown(summary: dict, outcomes: list[QueryOutcome], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{summary['run_id']}.md"

    m = summary["metrics"]
    label = {
        "faithfulness": "忠实度 Faithfulness",
        "answer_relevancy": "答案相关性 Answer Relevancy",
        "context_precision": "上下文精确率 Context Precision",
        "context_recall": "上下文召回率 Context Recall",
        "stale_recall_rate": "**过期误召回率 Stale Recall Rate**",
        "refusal_rate": "拒答准确率 Refusal Accuracy",
    }
    tgt = _targets()
    target = {
        k: ("—" if k not in tgt else f"{tgt[k]['op']}{tgt[k]['value']}")
        for k in m
    }

    lines = [
        f"# 评估报告 {summary['run_id']}",
        "",
        f"- 时间：{summary['created_at']}",
        f"- 数据集：{summary['dataset_size']} 条",
        f"- MRR：{_fmt(mrr(outcomes))}",
        "",
        "## 指标",
        "",
        "| 指标 | 值 | 目标 | 状态 |",
        "|---|---|---|---|",
    ]
    for key in m:
        value = m[key]
        ok = _verdict(key, value)
        lines.append(f"| {label.get(key, key)} | {_fmt(value)} | {target.get(key, '—')} | {ok} |")

    lines += ["", "## 分年度", "", "| 年份 | 过期误召回率 | 样本数 |", "|---|---|---|"]
    for row in summary["by_year"]:
        lines.append(
            f"| {row['year']} | {_fmt(row['stale_recall_rate'])} | {row['count']} |"
        )

    if summary["regressions"]:
        lines += ["", "## 劣化", "", "| 指标 | 变化 | 对比批次 |", "|---|---|---|"]
        for r in summary["regressions"]:
            lines.append(f"| {r['metric']} | {r['delta']:+.4f} | {r['vs_run']} |")

    fails = [o for o in outcomes if o.expected_doc_ids and not o.hit]
    if fails:
        lines += ["", "## 未召回期望文档的 query", "", "| query_id | 期望 | 实际 |", "|---|---|---|"]
        for o in fails:
            lines.append(
                f"| {o.query_id} | {', '.join(o.expected_doc_ids) or '—'} | "
                f"{', '.join(o.retrieved_doc_ids) or '（空）'} |"
            )

    stale_bad = [
        o for o in outcomes if o.stale_hits and not o.expect_stale_recall and o.retrieved_doc_ids
    ]
    if stale_bad:
        lines += ["", "## 不应召回过期版本却召回了的 query", "", "| query_id | 类别 | 过期条数 |", "|---|---|---|"]
        for o in stale_bad:
            lines.append(f"| {o.query_id} | {o.category} | {o.stale_hits} |")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _verdict(key: str, value: float | None) -> str:
    """按 thresholds.json 的 op 判定。阈值文件缺失/没登记该指标时给 "—"。"""
    if value is None:
        return "—"
    spec = _targets().get(key)
    if not spec:
        return "—"
    op, limit = spec.get("op"), spec.get("value")
    if op == "<=":
        return "✓" if value <= limit else "✗"
    if op == ">=":
        return "✓" if value >= limit else "✗"
    return "—"
