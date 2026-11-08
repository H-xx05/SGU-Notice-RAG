"""数据集校验的测试。这些规则是"跑批前的防线"，坏一条就会让整批结果不可信。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner import dataset as D


def write(tmp_path: Path, rows: list[dict], name: str = "q.jsonl") -> Path:
    p = tmp_path / name
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return p


def row(**kw) -> dict:
    base = {
        "query_id": "q01",
        "query": "转专业条件",
        "as_of": "2026-11-08",
        "expected_doc_ids": ["d1"],
        "expect_stale_recall": False,
        "category": "current_rule",
    }
    base.update(kw)
    return base


def trap(**kw) -> dict:
    return row(
        query_id="q99", category="cross_year_trap", expect_stale_recall=True,
        expected_doc_ids=["d2019"], **kw,
    )


def noans(**kw) -> dict:
    return row(
        query_id="q98", category="no_answer", expected_doc_ids=[],
        expect_refusal=True, **kw,
    )


class TestLoad:
    def test_minimal_valid(self, tmp_path: Path) -> None:
        p = write(tmp_path, [row(), trap(), noans()])
        rows = D.load(p)
        assert len(rows) == 3

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(D.DatasetError, match="不存在"):
            D.load(tmp_path / "nope.jsonl")

    def test_bad_json_reports_line(self, tmp_path: Path) -> None:
        p = tmp_path / "q.jsonl"
        p.write_text('{"ok": 1}\n{oops\n', encoding="utf-8")
        with pytest.raises(D.DatasetError, match=":2"):
            D.load(p)

    def test_blank_lines_and_comments_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "q.jsonl"
        p.write_text(
            "# 这是注释\n\n" + json.dumps(row()) + "\n\n" + json.dumps(trap()) + "\n"
            + json.dumps(noans()) + "\n",
            encoding="utf-8",
        )
        assert len(D.load(p)) == 3

    def test_empty_file_is_error(self, tmp_path: Path) -> None:
        p = tmp_path / "q.jsonl"
        p.write_text("\n\n", encoding="utf-8")
        with pytest.raises(D.DatasetError, match="空的"):
            D.load(p)


class TestValidate:
    def test_missing_field(self, tmp_path: Path) -> None:
        r = row()
        del r["as_of"]
        with pytest.raises(D.DatasetError, match="as_of"):
            D.load(write(tmp_path, [r, trap(), noans()]))

    def test_duplicate_id(self, tmp_path: Path) -> None:
        with pytest.raises(D.DatasetError, match="重复"):
            D.load(write(tmp_path, [row(), row()]))

    def test_unknown_category(self, tmp_path: Path) -> None:
        with pytest.raises(D.DatasetError, match="category"):
            D.load(write(tmp_path, [row(category="whatever"), trap(), noans()]))

    def test_no_answer_must_expect_refusal(self, tmp_path: Path) -> None:
        r = noans()
        del r["expect_refusal"]
        with pytest.raises(D.DatasetError, match="expect_refusal"):
            D.load(write(tmp_path, [row(), trap(), r]))

    def test_requires_cross_year_trap(self, tmp_path: Path) -> None:
        """核心指标是过期误召回率，没有陷阱题就没在测核心问题。"""
        with pytest.raises(D.DatasetError, match="cross_year_trap"):
            D.load(write(tmp_path, [row(), noans()]))

    def test_requires_no_answer(self, tmp_path: Path) -> None:
        with pytest.raises(D.DatasetError, match="no_answer"):
            D.load(write(tmp_path, [row(), trap()]))

    def test_expected_doc_ids_must_be_list(self, tmp_path: Path) -> None:
        with pytest.raises(D.DatasetError, match="数组"):
            D.load(write(tmp_path, [row(expected_doc_ids="d1"), trap(), noans()]))

    def test_expect_stale_recall_must_be_bool(self, tmp_path: Path) -> None:
        with pytest.raises(D.DatasetError, match="布尔"):
            D.load(write(tmp_path, [row(expect_stale_recall="false"), trap(), noans()]))

    def test_blank_query(self, tmp_path: Path) -> None:
        with pytest.raises(D.DatasetError, match="query"):
            D.load(write(tmp_path, [row(query="   "), trap(), noans()]))


class TestRealDataset:
    def test_repo_dataset_is_valid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        rows = D.load(root / "dataset" / "queries.jsonl")
        assert len(rows) >= 5, "元提示词要求至少 5 条"
        cats = {r["category"] for r in rows}
        assert "cross_year_trap" in cats
        assert "no_answer" in cats

    def test_to_outcomes_shape(self) -> None:
        rows = [row(), trap(), noans()]
        outs = D.to_outcomes(rows)
        assert outs[0].query_id == "q01"
        assert outs[1].expect_stale_recall is True
        assert outs[2].expect_refusal is True
