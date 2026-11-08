"""采集主流程：列表页翻页 → 详情页 → 时效抽取 → 切块 → 写 chunks.jsonl。

一次运行的完整链路（对应 docs/architecture.md 的 A 泳道）：

    crawl_pages()      受限翻页（Fetcher 四道闸门）
      └─ parse_detail()  正文 + 附件链接 + 发布时间
           └─ temporal.extract()   effective_period
                └─ doc_registry.assign_versions()/link_versions()  版本关系
                     └─ splitter.split_into_chunks()  切块
                          └─ ChunkWriter  写 jsonl（幂等）

**附件只记录链接、不下载**：`Detail.attachments` 里的 `url` 会保留，但流程不抓取附件内容。
理由是附件是 xlsx/pdf/doc，解析需要额外依赖与 OCR，而它们的**时效性由正文决定**（正文里的
有效期条款同样约束附件）。要纳入附件正文时在 A 的后续迭代里加，不要在本流程里顺手下载。

**为什么先全部读完再算版本**：`is_current` / `supersedes_doc_id` 依赖同批次内的
其他文档（"新版有没有写废止旧版"），逐篇流式处理会把关系算错。所以内存里先攒
一份 `RawDoc` 列表，最后统一算版本、统一切块、统一写盘。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .chunking.splitter import estimate_tokens, split_into_chunks
from .config import BASE_URL, LIST_PATH, Settings, load_settings
from .doc_registry import DocMeta, assign_versions, link_versions
from .emit.chunk_writer import ChunkRecord, ChunkWriter, chunk_id_of, sha256_text
from .fetcher import BlockedError, Fetcher
from .parsers.detail_page import Detail, parse_detail_page
from .parsers.list_page import ListPage, parse_list_page
from .temporal import extract as extract_period

log = logging.getLogger(__name__)


def now_iso() -> str:
    """RFC3339，带 +08:00（站点与团队都在东八区，避免 UTC 造成跨日误判）。"""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


@dataclass
class RawDoc:
    """一篇通知读完后的中间态（还没算版本、还没切块）。"""

    news_id: str
    title: str
    publish_date: str
    url: str
    paragraphs: list[str]
    period_from: str | None
    period_to: str | None
    period_precision: str

    @property
    def full_text(self) -> str:
        return "\n".join(self.paragraphs)


@dataclass
class CrawlResult:
    docs: list[RawDoc] = field(default_factory=list)
    chunk_written: int = 0
    chunk_skipped: int = 0
    pages_fetched: int = 0
    offsite_aborted: bool = False
    blocked: bool = False          # 被站点风控拦截（BlockedError），不是代码问题
    errors: list[str] = field(default_factory=list)


class Crawler:
    def __init__(self, settings: Settings | None = None, fetcher: Fetcher | None = None) -> None:
        self.settings = settings or load_settings()
        self.fetcher = fetcher or Fetcher(settings=self.settings)

    # ---------------------------------------------------------------- 列表

    def list_pages(self, max_pages: int | None = None) -> list[ListPage]:
        """翻列表页。`max_pages` 不得超过 `settings.max_pages_per_run`。"""
        cap = self.settings.max_pages_per_run
        if max_pages is not None:
            cap = min(cap, max(1, max_pages))

        pages: list[ListPage] = []
        url: str | None = BASE_URL + LIST_PATH
        seen_urls: set[str] = set()

        while url and len(pages) < cap:
            if url in seen_urls:  # 防环：站点分页不应成环，但便宜地防一下
                break
            seen_urls.add(url)
            html = self.fetcher.get(url, listing=True)
            page = parse_list_page(html, url)
            pages.append(page)
            if page.total_count is not None:
                log.info("列表共 %s 条，第 %s 页取到 %s 条", page.total_count, len(pages), len(page.items))
            url = page.next_url

        return pages

    # ---------------------------------------------------------------- 详情

    def read_detail(self, item_url: str, news_id: str, fallback_date: str) -> RawDoc | None:
        try:
            html = self.fetcher.get(item_url)
        except Exception as e:  # noqa: BLE001 - 单篇失败不应中断整批
            log.warning("详情抓取失败 %s：%s", item_url, e)
            return None

        d: Detail = parse_detail_page(html, news_id=news_id, source_url=item_url)
        publish = d.publish_date or fallback_date
        if not publish:
            log.warning("无发布日期，跳过 %s", item_url)
            return None

        period = extract_period(d.text, publish_date=publish)
        return RawDoc(
            news_id=news_id,
            title=d.title or f"(无标题) {news_id}",
            publish_date=publish,
            url=item_url,
            paragraphs=d.paragraphs,
            period_from=period.from_,
            period_to=period.to,
            period_precision=period.precision,
        )

    # ---------------------------------------------------------------- 主流程

    def run(
        self,
        *,
        out_path: str | Path = "data/chunks.jsonl",
        max_pages: int | None = None,
        dry_run: bool = False,
    ) -> CrawlResult:
        result = CrawlResult()

        try:
            pages = self.list_pages(max_pages=max_pages)
        except BlockedError as e:
            # 风控拦截：外部状态，改代码解决不了。单独标记，CLI 给出不同的退出码与指引。
            log.error("站点拦截：%s", e)
            result.blocked = True
            result.errors.append(str(e))
            return result
        except Exception as e:  # noqa: BLE001
            # 越界（OffsiteError）与页数上限（PageLimitExceeded）都必须冒到这里中止
            log.error("列表翻页中止：%s", e)
            result.offsite_aborted = True
            result.errors.append(str(e))
            return result

        for page in pages:
            for it in page.items:
                doc = self.read_detail(it.url, it.news_id, it.publish_date)
                if doc is not None:
                    result.docs.append(doc)

        result.pages_fetched = self.fetcher.pages_fetched

        if dry_run:
            log.info("dry-run：%s 篇，%s 页，未写盘", len(result.docs), result.pages_fetched)
            return result

        written, skipped = self.emit(result.docs, out_path=out_path)
        result.chunk_written = written
        result.chunk_skipped = skipped
        log.info("写盘完成：新写入 %s chunk，幂等跳过 %s", written, skipped)
        return result

    # ---------------------------------------------------------------- 版本 + 切块 + 写盘

    def emit(self, docs: list[RawDoc], *, out_path: str | Path) -> tuple[int, int]:
        if not docs:
            return (0, 0)

        metas: list[DocMeta] = assign_versions(
            [(d.news_id, d.title, d.publish_date) for d in docs]
        )
        link_versions(metas, {m.doc_id: d.full_text for m, d in zip(metas, docs, strict=True)})

        writer = ChunkWriter(Path(out_path))
        ingested = now_iso()
        written = skipped = 0

        for doc, meta in zip(docs, metas, strict=False):
            drafts = split_into_chunks(doc.title, doc.paragraphs)
            for seq, draft in enumerate(drafts, start=1):
                rec = ChunkRecord(
                    chunk_id=chunk_id_of(meta.doc_id, seq),
                    doc_id=meta.doc_id,
                    title=doc.title,
                    publish_date=doc.publish_date,
                    effective_period={
                        "from": doc.period_from,
                        "to": doc.period_to,
                        "precision": doc.period_precision,
                    },
                    doc_version=meta.doc_version,
                    doc_version_rank=meta.doc_version_rank,
                    section_path=draft.section_path,
                    content_type="notice_body",
                    source_url=doc.url,
                    text=draft.text,
                    token_count=estimate_tokens(draft.text),
                    content_hash=sha256_text(draft.text),
                    supersedes_doc_id=meta.supersedes_doc_id,
                    is_current=meta.is_current,
                    ingested_at=ingested,
                )
                if writer.add(rec):
                    written += 1
                else:
                    skipped += 1

        return (writer.written, writer.skipped)
