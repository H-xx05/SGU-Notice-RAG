"""入库 SQL 与建表脚本的一致性检查（不连数据库）。

为什么要有这个测试：`runner/db.py` 的 INSERT 列名和
`infra/postgres/init/001_schema.sql` 是两份手写文件，改一处忘另一处时
报错发生在**跑完 40 条评估之后**（那时已经花掉几分钟和一次限流额度），
而且报错是 psycopg 的 `column "xxx" of relation "eval_results" does not exist`，
看起来像数据库坏了。这里提前在 CI 里拦住。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

EVAL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EVAL_ROOT.parent
DB_MODULE = EVAL_ROOT / "runner" / "db.py"
SCHEMA = REPO_ROOT / "infra" / "postgres" / "init" / "001_schema.sql"


def _schema_columns(table: str) -> list[str]:
    """从建表脚本里抽出某张表的列名（按出现顺序）。"""
    text = SCHEMA.read_text(encoding="utf-8")
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\);", text, re.S)
    assert m, f"建表脚本里找不到 {table}"
    cols = []
    for line in m.group(1).splitlines():
        line = line.split("--")[0].strip().rstrip(",")
        if not line or line.startswith("CONSTRAINT") or line.startswith("PRIMARY KEY"):
            continue
        name = line.split()[0]
        if name.isidentifier():
            cols.append(name)
    return cols


def _insert_columns(source: str, table: str) -> list[str]:
    """从 db.py 的 `INSERT INTO <table> (...)` 里抽出列名。"""
    m = re.search(rf"INSERT INTO {table}\s*\((.*?)\)\s*VALUES", source, re.S)
    assert m, f"db.py 里找不到对 {table} 的 INSERT"
    return [c.strip() for c in m.group(1).split(",") if c.strip()]


@pytest.mark.skipif(not SCHEMA.exists(), reason="缺少建表脚本（本测试只在完整仓库里跑）")
class TestInsertMatchesSchema:
    def test_eval_runs_columns_exist(self) -> None:
        source = DB_MODULE.read_text(encoding="utf-8")
        known = set(_schema_columns("eval_runs"))
        used = _insert_columns(source, "eval_runs")
        assert used, "INSERT 列清单解析为空，正则可能失效"
        missing = [c for c in used if c not in known]
        assert not missing, f"db.py 往 eval_runs 写了建表脚本里没有的列：{missing}"

    def test_eval_results_columns_exist(self) -> None:
        source = DB_MODULE.read_text(encoding="utf-8")
        known = set(_schema_columns("eval_results"))
        used = _insert_columns(source, "eval_results")
        assert used, "INSERT 列清单解析为空，正则可能失效"
        missing = [c for c in used if c not in known]
        assert not missing, f"db.py 往 eval_results 写了建表脚本里没有的列：{missing}"

    def test_both_tables_keep_contract_required_columns(self) -> None:
        """契约要求 /api/v1/eval/summary 能返回 run_id/created_at/dataset_size/metrics。"""
        runs = set(_schema_columns("eval_runs"))
        for col in ("run_id", "created_at", "dataset_size", "metrics", "by_year", "regressions"):
            assert col in runs, f"eval_runs 缺列 {col}（/api/v1/eval/summary 依赖它）"
        results = set(_schema_columns("eval_results"))
        for col in ("run_id", "query_id", "refused", "stale_hit"):
            assert col in results, f"eval_results 缺列 {col}"

    def test_as_of_present_for_by_year(self) -> None:
        """by_year 按评估基准日分组；补全 as_of 才能按年聚合，不能靠 created_at 猜。"""
        assert "as_of" in set(_schema_columns("eval_results"))
