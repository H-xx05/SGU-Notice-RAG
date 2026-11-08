"""thresholds.json 的校验。

它是唯一权威来源，所以必须保证：
  1. 文件本身结构正确、目标齐全；
  2. `runtime` 里的数字与 rag-core 的默认值一致 —— 否则"评估说合格、线上却按另一套参数跑"，
     这是最难查的一类 bug。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = json.loads((ROOT / "thresholds.json").read_text(encoding="utf-8"))
RAG_CORE_SETTINGS = ROOT.parent / "rag-core" / "app" / "settings.py"

REQUIRED_TARGETS = {
    "stale_recall_rate",
    "refusal_rate",
    "faithfulness",
    "context_precision",
}


class TestShape:
    @pytest.mark.parametrize("key", sorted(REQUIRED_TARGETS))
    def test_target_present(self, key: str) -> None:
        assert key in THRESHOLDS["targets"], f"缺少目标指标 {key}"

    def test_op_is_comparable(self) -> None:
        for name, spec in THRESHOLDS["targets"].items():
            assert spec["op"] in {"<=", ">="}, f"{name} 的 op 必须是比较符"
            assert isinstance(spec["value"], int | float), f"{name} 的 value 必须是数字"

    def test_targets_match_prd(self) -> None:
        """数字来自 docs/prd.md 的成功指标，改这里必须同步改 PRD。"""
        t = THRESHOLDS["targets"]
        assert t["stale_recall_rate"]["value"] == 0.15
        assert t["refusal_rate"]["value"] == 0.90
        assert t["faithfulness"]["value"] == 0.85
        assert t["context_precision"]["value"] == 0.70

    def test_latency_present(self) -> None:
        assert THRESHOLDS["latency"]["p99_ms"] == 2000
        assert THRESHOLDS["latency"]["first_token_ms"] == 1000

    def test_dataset_minimums(self) -> None:
        """元提示词要求 queries.jsonl ≥5 条，且至少 1 条无答案 + 1 条跨年度陷阱。"""
        d = THRESHOLDS["dataset"]
        assert d["min_queries"] >= 5
        assert d["min_no_answer"] >= 1
        assert d["min_cross_year_trap"] >= 1


class TestRuntimeMatchesRagCore:
    """thresholds.json 的 runtime 段必须能被 rag-core 的默认值验证。"""

    @pytest.fixture(scope="class")
    def settings_source(self) -> str:
        if not RAG_CORE_SETTINGS.is_file():
            pytest.skip("rag-core 不在同一检出处，跳过跨模块一致性检查")
        return RAG_CORE_SETTINGS.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("field", "key"),
        [
            ("min_score", "min_answer_score"),
            ("stale_penalty", "stale_penalty"),
            ("rrf_k", "rrf_k"),
            ("dense_top_k", "dense_top_k"),
            ("sparse_top_k", "sparse_top_k"),
            ("rerank_top_k", "rerank_top_k"),
            ("final_top_k", "final_top_k"),
        ],
    )
    def test_setting_matches(self, settings_source: str, field: str, key: str) -> None:
        # 两种写法都要认：`min_score: float = 0.35` 与 `min_score: float = Field(default=0.35, ...)`
        m = re.search(
            rf"^\s*{field}\s*:\s*[\w\[\], |]+\s*=\s*(?:Field\(\s*default\s*=\s*)?([0-9.]+)",
            settings_source,
            re.M,
        )
        assert m, f"在 settings.py 里找不到 {field}"
        assert float(m.group(1)) == float(THRESHOLDS["runtime"][key]), (
            f"{field}={m.group(1)} 与 thresholds.json 的 {key}={THRESHOLDS['runtime'][key]} 不一致"
        )
