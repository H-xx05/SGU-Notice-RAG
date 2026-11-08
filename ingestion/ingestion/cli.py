"""A 组命令行入口：`python -m ingestion.cli <cmd>`。

三个子命令，都只做一件事：

    crawl  受限抓取一页/几页，产出 chunks.jsonl（默认 --max-pages 3，不是 50）
    check  校验已有 chunks.jsonl 是否符合契约（CI 用）
    stats  打印 chunks.jsonl 的统计（多少文档、现行/过期、时效精度分布）

**默认 `--max-pages 3`**：`MAX_PAGES_PER_RUN=50` 是上限，不是默认值。新人第一次跑
应该只碰 45 条通知，确认解析正确后再放大。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .config import (
    DEFAULT_MAX_PAGES_PER_RUN,
    DEFAULT_REQUEST_DELAY_SECONDS,
    load_settings,
)
from .emit.chunk_writer import (
    PAYLOAD_SHAPE_SENTINEL,
    is_payload_shape,
    period_of,
    read_chunks,
    validate_record,
)
from .fetcher import BlockedError, OffsiteError, PageLimitExceeded
from .pipeline import Crawler


def cmd_crawl(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.delay is not None:
        settings = settings.__class__(
            allowed_domains=settings.allowed_domains,
            request_delay_seconds=max(args.delay, DEFAULT_REQUEST_DELAY_SECONDS),
            max_pages_per_run=settings.max_pages_per_run,
            max_requests_per_run=settings.max_requests_per_run,
            data_dir=settings.data_dir,
        )

    crawler = Crawler(settings=settings)
    out = Path(args.out)

    try:
        result = crawler.run(out_path=out, max_pages=args.max_pages, dry_run=args.dry_run)
    except OffsiteError as e:
        print(f"✗ 域名越界，已中止（这是安全特性，不是 bug）：{e}", file=sys.stderr)
        return 2
    except PageLimitExceeded as e:
        print(f"✗ 已达页数上限：{e}", file=sys.stderr)
        return 3
    except BlockedError as e:
        _print_blocked(e)
        return 4

    if result.blocked:
        _print_blocked(result.errors[0] if result.errors else "")
        return 4

    if result.offsite_aborted:
        print(f"✗ 中止：{result.errors}", file=sys.stderr)
        return 2

    print(f"文档 {len(result.docs)} 篇 / 页 {result.pages_fetched} 张")
    if not args.dry_run:
        print(f"chunk 新写入 {result.chunk_written}，幂等跳过 {result.chunk_skipped} → {out}")
        problems = _check_file(out)
        if problems == [PAYLOAD_SHAPE_SENTINEL]:
            return 0
        if problems:
            print(f"✗ 写出的文件有 {len(problems)} 处不符合契约：", file=sys.stderr)
            for p in problems[:10]:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print("✓ 契约校验通过")
    return 0


def _print_blocked(detail: str) -> None:
    """拦截的运行指引。**不要**建议加大重试——那是把风控变成封禁的路径。"""
    print(
        "✗ 站点返回拦截页（HTTP 403 / WAF 拦截），本次采集已中止。\n"
        f"  详情：{detail}\n"
        "\n"
        "  这不是解析器的问题，改代码没有用。可能的处置方向：\n"
        "  1) 在浏览器里打开站点确认是否对校园网/本机 IP 放行；\n"
        "  2) 改用学校允许的渠道（如教务系统导出、线下拷贝），并遵守 robots 与授权要求；\n"
        "  3) 若已获得授权，向站点管理方申请放行。\n"
        "  在明确授权之前，**不要**提高并发、缩短延时或反复重试。",
        file=sys.stderr,
    )


def cmd_check(args: argparse.Namespace) -> int:
    problems = _check_file(Path(args.path))
    if problems == [PAYLOAD_SHAPE_SENTINEL]:
        return 0  # 形态不符但不是错误：已经打印了说明
    if problems:
        print(f"✗ {len(problems)} 处不符合契约：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"✓ {args.path} 符合契约")
    return 0


def _check_file(path: Path) -> list[str]:
    rows = read_chunks(path)
    if not rows:
        return [f"{path} 不存在或为空"]

    # 先认形态：扁平 `effective_from/to` 是 **Qdrant payload**，不是 A 产出的 chunks.jsonl。
    # 直接报"缺 6 个字段"会让人以为文件坏了，其实只是拿错了文件。
    if is_payload_shape(rows):
        print(
            f"… {path} 是 Qdrant payload 形态（扁平 effective_from/effective_to），"
            "不是 A 组产出的 chunks.jsonl。\n"
            "  payload 形态由 ChunkRecord.to_payload() 生成，本来就不带 "
            "schema_version / content_hash —— 这不是契约违规。\n"
            "  要校验 A 的产物，请对 ingestion/data/chunks.jsonl 运行本命令。",
            file=sys.stderr,
        )
        return [PAYLOAD_SHAPE_SENTINEL]  # 由调用方区分"跳过"与"失败"

    problems: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        for p in validate_record(row):
            problems.append(f"第 {i} 行：{p}")
        cid = row.get("chunk_id", "")
        if cid in seen:
            problems.append(f"第 {i} 行：chunk_id 重复 {cid}")
        seen.add(cid)
    return problems


def cmd_stats(args: argparse.Namespace) -> int:
    rows = read_chunks(Path(args.path))
    if not rows:
        print(f"✗ {args.path} 不存在或为空", file=sys.stderr)
        return 1

    docs = {r["doc_id"] for r in rows}
    current = sum(1 for r in rows if r.get("is_current"))
    periods = [period_of(r) for r in rows]
    precisions = Counter(p_["precision"] for p_ in periods)
    ranged = sum(1 for p_ in periods if p_["from"] and p_["to"])
    no_to = sum(1 for p_ in periods if not p_["to"])
    versions = Counter(r["doc_version"].split(".")[0] for r in rows)

    print(f"文件      {args.path}")
    print(f"chunk     {len(rows)}")
    print(f"文档      {len(docs)}")
    print(f"现行      {current}（{current / len(rows):.0%}）")
    print(f"过期      {len(rows) - current}")
    print(f"有完整区间 {ranged}（{ranged / len(rows):.0%}）")
    print(f"无终止日   {no_to}（长期有效或抽取失败，需人工抽查）")
    print(f"精度分布   {dict(precisions)}")
    print(f"按年分布   {dict(sorted(versions.items()))}")
    if args.json:
        print(json.dumps(
            {
                "chunks": len(rows),
                "docs": len(docs),
                "current": current,
                "precision": dict(precisions),
                "complete_range": ranged,
                "open_ended": no_to,
            },
            ensure_ascii=False,
        ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ingestion", description="SGU 教务通知采集（合规受限）")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="抓取并写出 chunks.jsonl")
    c.add_argument("--out", default="data/chunks.jsonl", help="输出路径")
    c.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help=f"本次最多翻几页列表（默认 3，硬上限 {DEFAULT_MAX_PAGES_PER_RUN}）",
    )
    c.add_argument("--delay", type=float, default=None, help="请求间隔秒（≥1，只能调大）")
    c.add_argument("--dry-run", action="store_true", help="只抓不写")
    c.set_defaults(func=cmd_crawl)

    k = sub.add_parser("check", help="校验 chunks.jsonl 是否符合契约")
    k.add_argument("path", nargs="?", default="data/chunks.jsonl")
    k.set_defaults(func=cmd_check)

    s = sub.add_parser("stats", help="打印 chunks.jsonl 统计")
    s.add_argument("path", nargs="?", default="data/chunks.jsonl")
    s.add_argument("--json", action="store_true", help="额外输出一行 JSON")
    s.set_defaults(func=cmd_stats)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
