"""详情页解析：`info/1050/{news_id}.htm` → [Detail]。

从真实页面核实到的结构（2026-11）：

```html
<div class="conth1">关于做好韶关学院第六届…的通知</div>
<div class="conthsj">作者：学籍管理科&nbsp;&nbsp;时间：2026-11-02&nbsp;&nbsp;</div>
<div id="vsb_content" class="concon"><div class="v_news_content">
  <p><span>校内各部门、二级学院：</span></p>
  <p><span>一、学位评定分委员会委员构成</span></p>
  ...
</div></div>
<div class="sxt"><UL><li>附件【<a href="/system/_content/download.jsp?…">附件1：…xlsx</a>】已下载…次</li></UL></div>
```

关键点：正文里的汉字被 **逐字拆进 `letter-spacing` 的多个 `<span>`**（编辑部排版遗留），
所以提取正文必须走"取文本内容"而不是"取某个 span 的 text"；`HTMLParser` 的
`handle_data` 天然会把它们拼回来，这正是不用 CSS 选择器的好处。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# conthsj 里的 `时间：2026-11-02`
_DATE_RE = re.compile(r"时间[:：]\s*(\d{4})-(\d{1,2})-(\d{1,2})")
# 附件项：附件【<a href="...">文件名</a>】；文件名以 附件1： 开头
_ATTACH_HREF_ATTR = "href"

# 正文里"落款日期"，用于 effective_from 的兜底线索（2026年11月2日）
_SIGN_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


@dataclass
class Attachment:
    name: str
    url: str


@dataclass
class Detail:
    news_id: str
    title: str
    publish_date: str  # YYYY-MM-DD，详情页 `时间：` 优先
    paragraphs: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    source_url: str = ""

    @property
    def text(self) -> str:
        return "\n".join(p for p in self.paragraphs if p)


class _DetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta_text = ""
        self.paragraphs: list[str] = []
        self.attachments: list[Attachment] = []

        # 正文容器：<div id="vsb_content"> … </div>
        self._in_content = 0
        self._content_depth = 0

        self._in_title = False
        self._in_meta = False
        self._buffer: list[str] = []

        # 附件区
        self._in_attach_div = 0
        self._attach_href: str | None = None
        self._attach_name: list[str] = []

        # 块级元素边界：遇到 </p> / </div> 就结算一段
        self._block_tags = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "br"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = (a.get("class") or "").split()

        if tag == "div":
            if a.get("id") == "vsb_content":
                self._in_content = 1
                self._content_depth = 1
                self._buffer = []
                return
            if self._in_content:
                self._content_depth += 1
            if "sxt" in cls:
                self._in_attach_div += 1
            if "conth1" in cls:
                self._in_title = True
                self._buffer = []
            elif "conthsj" in cls:
                self._in_meta = True
                self._buffer = []
        elif tag == "a" and self._in_attach_div:
            href = a.get("href") or ""
            if href:
                self._attach_href = href
                self._attach_name = []
        elif tag in self._block_tags and self._in_content:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            if self._in_content:
                self._content_depth -= 1
                if self._content_depth <= 0:
                    self._flush()
                    self._in_content = 0
            if self._in_attach_div:
                self._in_attach_div -= 1
            if self._in_title:
                self.title = "".join(self._buffer).strip()
                self._in_title = False
                self._buffer = []
            if self._in_meta:
                self.meta_text = "".join(self._buffer).strip()
                self._in_meta = False
                self._buffer = []
        elif tag == "a" and self._attach_href:
            name = "".join(self._attach_name).strip()
            if name:
                self.attachments.append(Attachment(name=name, url=self._attach_href))
            self._attach_href = None
            self._attach_name = []
        elif tag in ("p", "li", "tr", "h1", "h2", "h3", "h4"):
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title or self._in_meta:
            self._buffer.append(data)
            return
        if self._attach_href:
            self._attach_name.append(data)
        if self._in_content:
            self._buffer.append(data)

    def handle_entityref(self, name: str) -> None:
        self._append_entity(name)

    def handle_charref(self, name: str) -> None:
        self._append_entity(name, numeric=True)

    def _append_entity(self, name: str, numeric: bool = False) -> None:
        import html as _html

        ch = _html.unescape(f"&#{name};" if numeric else f"&{name};")
        if self._in_content:
            self._buffer.append(ch)

    def _flush(self) -> None:
        if not self._in_content or not self._buffer:
            return
        text = "".join(self._buffer)
        text = _normalize(text)
        if text:
            self.paragraphs.append(text)
        self._buffer = []


def _normalize(text: str) -> str:
    """合并空白、去掉 nbsp、去掉纯装饰行。"""
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    out = [ln for ln in lines if ln and ln not in {"&nbsp;", "·"}]
    return " ".join(out).strip()


def parse_detail_page(html: str, news_id: str, source_url: str) -> Detail:
    p = _DetailParser()
    p.feed(html)

    publish = ""
    m = _DATE_RE.search(p.meta_text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        publish = f"{y:04d}-{mo:02d}-{d:02d}"

    attachments = [
        Attachment(name=a.name, url=_abs(a.url, source_url))
        for a in p.attachments
    ]

    return Detail(
        news_id=news_id,
        title=p.title,
        publish_date=publish,
        paragraphs=p.paragraphs,
        attachments=attachments,
        source_url=source_url,
    )


def _abs(href: str, base: str) -> str:
    from ..fetcher import absolute_url

    return absolute_url(href, base)


def sign_date_of(text: str) -> str | None:
    """正文末尾的落款日期（`2026年11月2日`），用于 effective_from 的兜底线索。

    只取**最后一次**出现的完整年月日——落款总在文末，正文中间的引用日期不算。
    """
    matches = list(_SIGN_DATE_RE.finditer(text))
    if not matches:
        return None
    m = matches[-1]
    y, mo, d = (int(x) for x in m.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"
