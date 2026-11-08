"""抓取闸门：域名白名单、串行限速、页数上限。

**所有 HTTP 请求都必须经过 `Fetcher.get()`**，不允许在别处直接 urllib/requests。
这是元提示词"采集合规"唯一可审计的落点：只要绕开它，三条硬参数就形同虚设。

## 关于 `MAX_PAGES_PER_RUN` 的语义（这里踩过坑，写下来）

元提示词里这个参数是"单次运行最多翻几页**列表**"。但 `Fetcher` 拿不到"这一页是
列表还是详情"的信息——它只看见 URL。如果按"每个 HTTP 请求都计数"来算，默认 50
在列表 15 条/页的站上，翻到第 1 页就会因为 15 篇详情 + 后续翻页而把额度吃光，
**后面的通知被静默丢弃**（只留一行 warning）——这种 bug 不会报错，只会让数据
莫名其妙变少。

所以这里把两个额度**分开**：

- `max_pages_per_run`：列表页翻页数，由 `Crawler.list_pages()` 自己把关；
- `max_requests_per_run`：总请求数硬闸门，防止详情页把整站拖下来，
  由 `Fetcher` 在每次请求前检查。

两者都是"只能调小"的保守值。`Crawler` 用 `fetch_policy` 告诉 `Fetcher`
当前请求属于哪一类，`Fetcher` 据此只对详情请求计请求额。
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import MAX_RETRIES, REQUEST_TIMEOUT_SECONDS, Settings


class OffsiteError(RuntimeError):
    """URL 不在 `ALLOWED_DOMAINS` 内。**必须中止整个运行**，不是跳过。"""


class PageLimitExceeded(RuntimeError):
    """本次运行已用满 `MAX_PAGES_PER_RUN`。"""


class BlockedError(RuntimeError):
    """站点返回了拦截页（WAF/风控）。

    与 `FetchError` 分开是刻意的：2026-11 实测 `https://www.sgu.edu.cn/**` 全部
    返回 HTTP 403 + 一个标题为 `block` 的 47KB 静态页（不是通知页）。把这种情况
    报成"解析失败"会让下一个维护者去改解析器——方向完全错了。拦截属于**不可
    由代码绕过的外部状态**：正确反应是停下来、告知、等人工处理，不要加大频率重试。
    """


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def is_allowed(url: str, allowed_domains: tuple[str, ...]) -> bool:
    """域名匹配：`sgu.edu.cn` 同时允许 `www.sgu.edu.cn`、`jwb.sgu.edu.cn`。

    不允许 `evil-sgu.edu.cn`（不用简单的 endswith 判断，改用点分段比较）。
    """
    host = host_of(url)
    if not host:
        return False
    for d in allowed_domains:
        d = d.lower()
        if host == d or host.endswith("." + d):
            return True
    return False


@dataclass
class Fetcher:
    """串行抓取 + 限速。**没有 HTTP 连接池，没有并发**——这是刻意的。"""

    settings: Settings
    _last_request_at: float = 0.0
    _pages_fetched: int = 0      # 列表页翻页数
    _requests_made: int = 0      # 总 HTTP 请求数（含详情）
    _sleep: Callable[[float], None] = field(default=time.sleep)

    # ---------------------------------------------------------------- 闸门

    def _guard(self, url: str) -> None:
        if not is_allowed(url, self.settings.allowed_domains):
            # 越界即中止：不是 try/except 里吞掉的异常，是要冒到 CLI 的致命错误
            raise OffsiteError(
                f"URL 越界：{url}（host={host_of(url)!r} 不在 {self.settings.allowed_domains}）"
            )
        if self._requests_made >= self.settings.max_requests_per_run:
            raise PageLimitExceeded(
                "本次运行已达总请求上限 "
                f"MAX_REQUESTS_PER_RUN={self.settings.max_requests_per_run}"
            )

    def _throttle(self) -> None:
        """两次请求之间至少间隔 `request_delay_seconds`。"""
        elapsed = time.monotonic() - self._last_request_at
        wait = self.settings.request_delay_seconds - elapsed
        if wait > 0:
            self._sleep(wait)

    # ---------------------------------------------------------------- 抓取

    def get(self, url: str, *, listing: bool = False) -> str:
        """抓一页，返回解码后的文本。失败重试 `MAX_RETRIES` 次，之后抛 `FetchError`。

        `listing=True` 表示这是列表页——只影响计数口径（列表页数 vs 总请求数），
        不影响闸门本身。默认 `False`，因为绝大多数请求是详情页。
        """
        self._guard(url)
        last_error: Exception | None = None
        for _attempt in range(MAX_RETRIES + 1):
            self._throttle()
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": self.settings.user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                        # 站点声明的编码是 UTF-8，但老页面偶有 GBK；显式声明不接受压缩
                        "Accept-Language": "zh-CN,zh;q=0.9",
                    },
                )
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                    raw = resp.read()
                    charset = _charset_of(resp.headers.get("Content-Type", ""))
                    status = resp.status
                self._last_request_at = time.monotonic()
                self._requests_made += 1
                if listing:
                    self._pages_fetched += 1
                text = _decode(raw, charset)
                if looks_blocked(status, text):
                    raise BlockedError(
                        f"站点返回拦截页：{url}（HTTP {status}，{len(raw)} 字节，"
                        "标题/内容含 WAF 拦截特征）。这是外部风控，不要靠提高重试次数绕过。"
                    )
                return text
            except urllib.error.HTTPError as e:
                last_error = e
                self._last_request_at = time.monotonic()
                self._requests_made += 1
                if e.code in (403, 429):
                    # 403/429 多半是风控，直接升级为 BlockedError，别当成普通 4xx 静默吞掉
                    raise BlockedError(
                        f"站点拒绝访问：{url}（HTTP {e.code}）。疑似风控/限流，请人工确认后再跑。"
                    ) from e
                if 400 <= e.code < 500:
                    break  # 4xx 重试无意义
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = e
                self._last_request_at = time.monotonic()
                self._requests_made += 1

        raise FetchError(f"抓取失败：{url}（{last_error}）") from last_error

    @property
    def pages_fetched(self) -> int:
        """列表页翻了几个。"""
        return self._pages_fetched

    @property
    def requests_made(self) -> int:
        """总共发出去多少次 HTTP 请求（含详情页与失败重试）。"""
        return self._requests_made


class FetchError(RuntimeError):
    pass


# 实测的拦截页特征：<title>block</title>，正文只有一段居中的 SVG/提示，没有 <li> 通知条目
_BLOCK_TITLE = re.compile(r"<title>\s*block\s*</title>", re.I)
_BLOCK_HINTS = ("访问被拦截", "请求被拦截", "您的访问", "Access Denied", "Request blocked")


def looks_blocked(status: int, text: str) -> bool:
    """判断一页是不是 WAF 拦截页而不是真实内容。

    判定用**双重证据**，避免把正常页面误判：HTTP 非 2xx，或（标题为 block），
    或（命中拦截语且页面里一条通知链接都没有）。
    """
    if status in (403, 429):
        return True
    if _BLOCK_TITLE.search(text[:4000]):
        return True
    head = text[:4000]
    if any(h in head for h in _BLOCK_HINTS):
        return "/info/1050/" not in text
    return False


def _charset_of(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        k, _, v = part.strip().partition("=")
        if k.lower() == "charset" and v:
            return v.strip().strip('"').lower()
    return "utf-8"


_REPLACEMENT = "\ufffd"


def _plausibility(text: str) -> float:
    """给解出来的文本打一个"像不像正常中文网页"的**平均**分（每字符）。

    为什么需要这一步：GBK 的字节流**可能恰好是合法的 UTF-8**。例如"通知"的
    GBK 编码是 `CD A8 D6 AA`，按 UTF-8 解码不报错，得到两个组合附加符号
    `\u0368\u01aa`——没有异常、没有替换符，却是彻底的乱码。只靠 try/except
    的尝试顺序会静默产出坏数据。

    **必须按字符平均、不能求和**：反例是 UTF-8 的"通知"（6 字节），按 gb18030
    会解成 3 个汉字"閫氱煡"，求和得分反而更高。平均分下二者相同，再由"声明编码
    优先"的平手规则裁决，结果就对了。
    """
    if not text:
        return 0.0
    score = 0.0
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:            # CJK 汉字
            score += 2.0
        elif 0x3000 <= cp <= 0x303F:          # 中文标点
            score += 2.0
        elif 0xFF00 <= cp <= 0xFFEF:          # 全角字符
            score += 2.0
        elif 0x20 <= cp <= 0x7E or ch in "\r\n\t":  # ASCII 可打印
            score += 1.0
        elif 0x0300 <= cp <= 0x036F:          # 组合附加符号：GBK→UTF-8 乱码的典型产物
            score -= 4.0
        elif 0x80 <= cp <= 0x024F:            # Latin-1 扩展：中文页面几乎不会大量出现
            score -= 3.0
        elif ch == _REPLACEMENT:
            score -= 5.0
    return score / len(text)


def _decode(raw: bytes, charset: str) -> str:
    """解码容错：在候选编码里挑"最像正常文本"的那个，而不是挑第一个不报错的。

    候选顺序只用于**打平时**的优先（声明编码 > utf-8 > gb18030）；
    真正的取舍看 `_plausibility` 的平均分。这样既保住正常页面的速度，
    也不会被"GBK 恰好是合法 UTF-8"这类情况骗过去。
    """
    forced: str | None = None
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
        forced = "utf-8"  # 有 BOM 就是 UTF-8，别再猜

    candidates: list[str] = []
    for enc in ([forced] if forced else []) + [charset, "utf-8", "gb18030"]:
        if enc and enc not in candidates:
            candidates.append(enc)

    best: str | None = None
    best_score = float("-inf")
    for enc in candidates:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        score = _plausibility(text)
        # 严格大于：打平时保留先出现的候选，即声明编码优先
        if score > best_score:
            best, best_score = text, score

    if best is not None:
        return best
    return raw.decode("utf-8", errors="replace")


def absolute_url(href: str, base: str) -> str:
    """把列表页的相对链接补成绝对 URL（`jxtz/96.htm` → 绝对地址）。"""
    return urllib.parse.urljoin(base, href)
