"""评估集与检索语料的 id 对齐检查。

为什么需要这个测试：E 组的 `dataset/queries.jsonl` 里写的是 `expected_doc_ids`，
B 组的 `rag-core/tests/fixtures/chunks.jsonl` 是骨架期唯一能离线跑的语料。
两份文件由不同的人维护，**只要有一边改了 doc_id 的形态，E 的指标就会全线归零**，
而现象只是"context_recall 变成 0"，看起来像检索坏了，实际是两套语料对不上。

这个测试把这条隐性耦合变成显性断言：语料在时就检查，语料不在就跳过
（比如只签出一部分的机器上）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

EVAL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EVAL_ROOT.parent
DATASET = EVAL_ROOT / "dataset" / "queries.jsonl"
CORPUS = REPO_ROOT / "rag-core" / "tests" / "fixtures" / "chunks.jsonl"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def corpus_doc_ids() -> set[str]:
    if not CORPUS.is_file():
        pytest.skip(f"检索语料不存在，跳过对齐检查：{CORPUS}")
    return {row["doc_id"] for row in _rows(CORPUS)}


@pytest.fixture(scope="module")
def dataset() -> list[dict]:
    return _rows(DATASET)


def test_every_expected_doc_exists_in_corpus(dataset: list[dict], corpus_doc_ids: set[str]) -> None:
    """评估集点名的每份文档都必须在语料里真实存在。

    缺一份的后果：这道题永远召不回来，`context_recall` 被永久拉低，
    而人工看报告时只会觉得"检索效果不好"。
    """
    missing: dict[str, list[str]] = {}
    for row in dataset:
        absent = [d for d in row.get("expected_doc_ids") or [] if d not in corpus_doc_ids]
        if absent:
            missing[row["query_id"]] = absent
    assert not missing, f"评估集点名了语料里不存在的文档：{missing}"


def test_doc_id_shape_is_consistent(dataset: list[dict], corpus_doc_ids: set[str]) -> None:
    """两侧的 doc_id 必须是同一种形态。

    契约规定 `jwb-jxtz-{发布年}-{站点 news_id}`。骨架期曾在语料里用过
    `jwb-jxtz-2026-0007` 这种自增占位 id，而评估集用的是站点 news_id
    （`jwb-jxtz-2026-29459`）—— 两边都"像"合法的 doc_id，但永远对不上。
    """
    corpus = {d.split("-")[-1] for d in corpus_doc_ids}
    dataset_tail = {
        d.split("-")[-1] for row in dataset for d in (row.get("expected_doc_ids") or [])
    }
    shared = corpus & dataset_tail
    assert shared, (
        "评估集与语料没有任何共同的 news_id —— 两侧的 doc_id 形态不一致。"
        f"语料尾段样例：{sorted(corpus)[:5]}；评估集尾段样例：{sorted(dataset_tail)[:5]}"
    )


def test_no_answer_rows_have_no_expected_docs(dataset: list[dict]) -> None:
    """无答案题不能点名任何文档 —— 点名了就等于给它一个"正确答案"，指标会被算成召回失败。"""
    bad = [
        row["query_id"]
        for row in dataset
        if row.get("expect_refusal") and (row.get("expected_doc_ids") or [])
    ]
    assert not bad, f"无答案题不应有 expected_doc_ids：{bad}"
