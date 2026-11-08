"""可选：用 RAGAS 算生成质量指标（faithfulness / answer_relevancy）。

**不进 CI**。原因写在下面，改之前先读完：

- RAGAS 需要 LLM judge。骨架阶段没有可用的 judge 配置，CI 里跑就要联网+花钱，
  而且结果不稳定（同一个答案跑两次差 0.05 是常态）。
- 本项目的验收核心是 stale_recall_rate / refusal_rate，这两个是确定性的，
  在 metrics.py 里已经算完。faithfulness 只是辅助信号。
- 所以：本地按需跑，跑完**回填**到同一份 report JSON 里，D 的看板就能显示。

用法：
    pip install "ragas>=0.2" datasets
    python -m eval.runner.cli ragas --report report/run-xxx.json --llm <model>
"""

from __future__ import annotations

from typing import Any


class RagasUnavailable(RuntimeError):
    pass


def compute_ragas(
    samples: list[dict[str, Any]],
    *,
    llm_model: str | None = None,
    embedding_model: str | None = None,
) -> dict[str, float | None]:
    """算 faithfulness 与 answer_relevancy。

    samples 每条需含：`user_input`、`response`、`retrieved_contexts`。
    """
    try:
        from ragas import EvaluationDataset, evaluate  # type: ignore[import-not-found]
        from ragas.metrics import answer_relevancy, faithfulness  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RagasUnavailable(
            "未安装 ragas。生成类指标是可选项：pip install 'ragas>=0.2' datasets"
        ) from exc

    if not samples:
        return {"faithfulness": None, "answer_relevancy": None}

    dataset = EvaluationDataset.from_list(samples)
    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy],
        raise_exceptions=False,
    )
    frame = result.to_pandas()
    # RAGAS 返回逐条分数，取均值；空列（全失败）要容错
    return {
        "faithfulness": _mean(frame.get("faithfulness")),
        "answer_relevancy": _mean(frame.get("answer_relevancy")),
    }


def _mean(series: Any) -> float | None:
    if series is None:
        return None
    try:
        value = float(series.mean())
    except (TypeError, ValueError):
        return None
    return None if value != value else round(value, 4)  # NaN 检查


def to_ragas_samples(rows: list[dict], outcomes: list[Any]) -> list[dict]:
    """把我们的观测转成 RAGAS 的形状。

    `retrieved_contexts` 用不到完整正文时只放标题 —— RAGAS 只看文本相似度，
    骨架期没必要把整站正文灌进去。
    """
    by_id = {o.query_id: o for o in outcomes}
    samples = []
    for row in rows:
        o = by_id.get(row["query_id"])
        if o is None or not o.answer:
            continue
        samples.append(
            {
                "user_input": row["query"],
                "response": o.answer,
                "retrieved_contexts": [d for d in o.retrieved_doc_ids] or ["（无召回）"],
            }
        )
    return samples
