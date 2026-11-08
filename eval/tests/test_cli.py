"""CLI 的 smoke test：check / baseline 必须在无网络、无数据库时也能过。"""

from __future__ import annotations

from pathlib import Path

from runner import cli

ROOT = Path(__file__).resolve().parents[1]


class TestCheck:
    def test_real_dataset_passes(self) -> None:
        assert cli.main(["check", "--dataset", str(ROOT / "dataset" / "queries.jsonl")]) == 0

    def test_missing_dataset_fails(self, tmp_path: Path) -> None:
        assert cli.main(["check", "--dataset", str(tmp_path / "nope.jsonl")]) == 1


class TestBaseline:
    def test_baseline_matches(self) -> None:
        """指标代码的黄金用例。改口径必须先改这里的期望值并在 PR 里说明理由。"""
        assert cli.main(["baseline"]) == 0
