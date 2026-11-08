"""列表页解析：`jxtz.htm` → [ListItem]。

从真实页面核实到的结构（2026-11）：

```html
<div class="list fl">
  <UL>
    <li>
      <a href="info/1050/29459.htm" target="_blank" title="关于做好…的通知">关于做好…的通知</a>
      <span class="date">2026-11-02</span>
    </li>
    ...
  </UL>
  <div class="pb_sys_common …">
    <span class="p_t">共1452条</span>
    <span class="p_no"><a href="jxtz/96.htm">2</a></span>   ← 注意：页号倒序
    <span class="p_last p_fun"><a href="jxtz/1.htm">尾页</a></span>
  </div>
</div>
```

两个必须写进代码的事实：
1. **分页是倒序的**：第 2 页是 `jxtz/96.htm`，最后一页是 `jxtz/1.htm`。所以"下一批更新的通知"
   在页号**更小**的那一边，靠解析 `下页` 链接拿地址，**绝不自己拼页号**。
2. 列表本身**不需要 JS**：`<UL>` 里的 `<li>` 是服务端渲染的静态内容。页面里的 Vue
   （`#appu3`）只负责顶部搜索框，与通知列表无关——不要为了"渲染页面"引入 Playwright。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

# info/1050/29459.htm -> 29459
_NEWS_ID_RE = re.compile(r"(?:^|/)info/1050/(\d+)\.htm")
_TOTAL_RE = re.compile(r"共\s*(\d+)\s*条")
_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


@dataclass(frozen=True)
class ListItem:
    """列表页一行。`publish_date` 是列表页给出的日期（详情页可能有更精确的，见 detail.py）。"""

    news_id: str
    title: str
    publish_date: str  # YYYY-MM-DD
    url: str


@dataclass
class ListPage:
    items: list[ListItem]
    total_count: int | None  # `共1452条` 里的 1452
    next_url: str | None  # `下页` 的绝对 URL；None = 已到最后一页


class _ListParser(HTMLParser):
    """只认 `list` 容器里的 `<li><a><span class=date>`，不建 DOM 树。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[ListItem] = []
        self.total_text: str = ""
        self._in_list_div = 0
        self._in_li = False
        self._in_date = False
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._date_text: list[str] = []
        self._pending_title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "div":
            if self._in_list_div == 0 and "list" in cls.split():
                self._in_list_div = 1
                return
            if self._in_list_div:
                self._in_list_div += 1
            return
        if not self._in_list_div:
            return
        if tag == "li":
            self._in_li = True
            self._anchor_href = None
            self._anchor_text = []
            self._date_text = []
            self._pending_title = ""
        elif tag == "a" and self._in_li:
            href = a.get("href") or ""
            if _NEWS_ID_RE.search(href):
                self._anchor_href = href
                self._pending_title = (a.get("title") or "").strip()
        elif tag == "span" and self._in_li and "date" in cls.split():
            self._in_date = True
            self._date_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_list_div:
            self._in_list_div -= 1
        elif tag == "span":
            self._in_date = False
        elif tag == "a":
            pass
        elif tag == "li" and self._in_li:
            self._in_li = False
            if self._anchor_href:
                m = _NEWS_ID_RE.search(self._anchor_href)
                if m:
                    title = self._pending_title or "".join(self._anchor_text).strip()
                    date = _norm_date("".join(self._date_text).strip())
                    if title and date:
                        self.items.append(
                            ListItem(
                                news_id=m.group(1),
                                title=title,
                                publish_date=date,
                                url="",  # 由调用方补绝对地址（解析器不知道 base）
                            )
                        )
            self._anchor_href = None

    def handle_data(self, data: str) -> None:
        if not self._in_list_div:
            return
        if self._in_date:
            self._date_text.append(data)
            return
        if self._anchor_href is not None:
            self._anchor_text.append(data)
            return
        if "共" in data and "条" in data:
            self.total_text = data


def _norm_date(s: str) -> str:
    m = _DATE_RE.search(s)
    if not m:
        return ""
    y, mo, d = (int(x) for x in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}"


def parse_list_page(html: str, page_url: str) -> ListPage:
    """解析一页列表。`page_url` 用于把相对 href 补成绝对地址。"""
    from ..fetcher import absolute_url

    p = _ListParser()
    p.feed(html)

    items = [
        ListItem(news_id=it.news_id, title=it.title, publish_date=it.publish_date,
                 url=absolute_url(f"info/1050/{it.news_id}.htm", page_url))
        for it in p.items
    ]

    total = None
    m = _TOTAL_RE.search(html)
    if m:
        total = int(m.group(1))

    return ListPage(items=items, total_count=total, next_url=_find_next_url(html, page_url))


def _find_next_url(html: str, page_url: str) -> str | None:
    """从 `下页` 链接里取下一页地址。

    **不拼页号**：分页是倒序的（第 2 页 = jxtz/96.htm），拼 `page+1` 会直接跳到最后一页。
    首页的"上页/下页"是 `<span class="p_prev_d p_fun_d">`（带 `_d` 后缀 = disabled），
    只有真正的链接才是 `<a>`。
    """
    from ..fetcher import absolute_url

    m = re.search(
        r'<span class="p_next p_fun">\s*<a href="([^"]+)"[^>]*>\s*下页\s*</a>',
        html,
    )
    if not m:
        return None
    return absolute_url(m.group(1), page_url)


def find_last_page_url(html: str, page_url: str) -> str | None:
    """`尾页` 链接。首页抓取时用它估算需要翻多少页（配合 MAX_PAGES_PER_RUN 上限）。"""
    from ..fetcher import absolute_url

    m = re.search(r'<span class="p_last p_fun">\s*<a href="([^"]+)"[^>]*>\s*尾页\s*</a>', html)
    if not m:
        return None
    return absolute_url(m.group(1), page_url)
