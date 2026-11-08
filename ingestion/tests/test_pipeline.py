"""离线端到端：用真实页面片段喂给完整 pipeline，产出必须符合契约。

站点 2026-11 起对非浏览器访问返回 WAF 拦截页（HTTP 403），所以 CI 里**不能**
依赖真实抓取。这个测试把两条真实 URL 的响应替换成固定样本，走的仍然是完整的
`Crawler.run()` → `ChunkWriter` → `validate_record` 链路，等于把"解析→时效→
分块→版本→落盘"整条流水线钉住。
"""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.config import Settings
from ingestion.fetcher import Fetcher
from ingestion.pipeline import Crawler

LIST_HTML = """
<div class="list fl"><UL>
<li><a href="info/1050/29459.htm" title="关于做好2026年转专业工作的通知">关于做好2026年转专业工作的通知</a>
<span class="date">2026-03-01</span></li>
<li><a href="info/1050/29001.htm" title="关于做好2025年转专业工作的通知">关于做好2025年转专业工作的通知</a>
<span class="date">2025-03-02</span></li>
</UL>
<div class="pb_sys_common"><span class="p_t">共2条</span>
<span class="p_no_d">1</span></div></div>
"""

DETAIL_NEW = """
<div class="conth1">关于做好2026年转专业工作的通知</div>
<div class="conthsj">作者：学籍管理科&nbsp;&nbsp;时间：2026-03-01</div>
<div id="vsb_content"><div class="v_news_content">
<p>一、申请条件</p>
<p>全日制本科一年级、二年级学生，已修课程平均学分绩点不低于2.5的，可以申请转专业。</p>
<p>二、报名安排</p>
<p>报名时间为2026年3月4日至2026年5月31日，逾期不再受理。</p>
<p>原《关于做好2025年转专业工作的通知》同时废止。</p>
<div class="sxt"><UL><li>附件【<a href="/system/x.xlsx">名单.xlsx</a>】</li></UL></div>
</div></div>
"""

DETAIL_OLD = """
<div class="conth1">关于做好2025年转专业工作的通知</div>
<div class="conthsj">作者：学籍管理科&nbsp;&nbsp;时间：2025-03-02</div>
<div id="vsb_content"><div class="v_news_content">
<p>一、申请条件</p>
<p>全日制本科一年级学生，已修课程平均学分绩点不低于2.8的，可以申请转专业。</p>
<p>二、报名安排</p>
<p>报名时间为2025年3月5日至2025年5月30日。</p>
</div></div>
"""

BASE = "https://www.sgu.edu.cn/jwb/"
PAGES = {
    BASE + "jxtz.htm": LIST_HTML,
    BASE + "info/1050/29459.htm": DETAIL_NEW,
    BASE + "info/1050/29001.htm": DETAIL_OLD,
}


class _FakeFetcher(Fetcher):
    """只替换网络层，闸门/限速/计数的逻辑仍然是真实的 `Fetcher.get`。"""

    def get(self, url: str, *, listing: bool = False) -> str:
        self._guard(url)
        self._requests_made += 1
        if listing:
            self._pages_fetched += 1
        return PAGES[url]


def _settings(tmp: Path) -> Settings:
    return Settings(
        allowed_domains=("sgu.edu.cn",),
        request_delay_seconds=1.0,
        max_pages_per_run=2,
        data_dir=str(tmp),
    )


def test_end_to_end_contract(tmp_path: Path) -> None:
    out = tmp_path / "chunks.jsonl"
    crawler = Crawler(settings=_settings(tmp_path), fetcher=_FakeFetcher(settings=_settings(tmp_path)))
    result = crawler.run(out_path=out, max_pages=1, dry_run=False)

    assert not result.errors
    assert len(result.docs) == 2
    assert out.exists()

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    assert rows, "没有写出任何 chunk"

    # 契约校验（A 自己的校验器）
    from ingestion.emit.chunk_writer import validate_record

    for r in rows:
        assert validate_record(r) == [], f"{r.get('chunk_id')}: {validate_record(r)}"

    # 时效：新版有显式区间，旧版也有
    new_chunks = [r for r in rows if r["doc_id"].endswith("29459")]
    assert any(r["effective_period"]["from"] == "2026-03-04" for r in new_chunks)
    assert any(r["effective_period"]["to"] == "2026-05-31" for r in new_chunks)

    # 版本链：新版正文写了"原…废止"，旧版应被标为过期
    old_chunks = [r for r in rows if r["doc_id"].endswith("29001")]
    assert old_chunks
    assert all(r["is_current"] is False for r in old_chunks)
    assert all(r["supersedes_doc_id"] is None for r in old_chunks)
    assert any(r["supersedes_doc_id"] == "jwb-jxtz-2025-29001" for r in new_chunks)

    # 每篇至少一个 chunk，且都带标题
    assert all(r["text"].startswith(r["title"]) for r in rows)


def test_idempotent_rerun(tmp_path: Path) -> None:
    """再跑一次不应重复写：重跑去重靠 chunk_id + content_hash。"""
    out = tmp_path / "chunks.jsonl"
    for _ in range(2):
        crawler = Crawler(
            settings=_settings(tmp_path), fetcher=_FakeFetcher(settings=_settings(tmp_path))
        )
        crawler.run(out_path=out, max_pages=1, dry_run=False)

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    ids = [r["chunk_id"] for r in rows]
    assert len(ids) == len(set(ids)), "重复运行写出了重复 chunk_id"
