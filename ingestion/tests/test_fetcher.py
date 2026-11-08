"""抓取闸门的行为测试：白名单、限速、页数上限、拦截页识别。

这些是元提示词"采集合规"的落地断言。**一旦有人放松这里的约束，测试必须红**。
全部离线：`_sleep` 被替换成记录器，`get()` 用本地假响应，不发真实请求。
"""

from __future__ import annotations

import pytest

from ingestion.config import Settings, load_settings
from ingestion.fetcher import (
    BlockedError,
    OffsiteError,
    PageLimitExceeded,
    _decode,
    absolute_url,
    host_of,
    is_allowed,
    looks_blocked,
)

SETTINGS = Settings(
    allowed_domains=("sgu.edu.cn",),
    request_delay_seconds=1.0,
    max_pages_per_run=3,
)


class TestDomainWhitelist:
    def test_exact_and_subdomain(self) -> None:
        assert is_allowed("https://www.sgu.edu.cn/jwb/jxtz.htm", ("sgu.edu.cn",))
        assert is_allowed("https://sgu.edu.cn/x", ("sgu.edu.cn",))
        assert is_allowed("https://jwb.sgu.edu.cn/x", ("sgu.edu.cn",))

    def test_lookalike_host_rejected(self) -> None:
        """`evil-sgu.edu.cn` 不能被当成内部域名。"""
        assert not is_allowed("https://evil-sgu.edu.cn/x", ("sgu.edu.cn",))
        assert not is_allowed("https://sgu.edu.cn.evil.com/x", ("sgu.edu.cn",))

    def test_offsite_raises_and_aborts(self) -> None:
        from ingestion.fetcher import Fetcher

        f = Fetcher(settings=SETTINGS, _sleep=lambda _s: None)
        with pytest.raises(OffsiteError):
            f.get("https://example.com/x")

    def test_host_of(self) -> None:
        assert host_of("https://www.sgu.edu.cn/a/b.htm") == "www.sgu.edu.cn"
        assert host_of("not a url") == ""


class TestPageLimit:
    def test_request_limit_raises(self) -> None:
        """总请求数闸门：详情页也会计数，防止一次跑把整站拖下来。"""
        from ingestion.fetcher import Fetcher

        f = Fetcher(settings=SETTINGS, _sleep=lambda _s: None)
        f._requests_made = SETTINGS.max_requests_per_run  # 已用满
        with pytest.raises(PageLimitExceeded):
            f.get("https://www.sgu.edu.cn/jwb/jxtz.htm", listing=True)

    def test_listing_counts_pages(self) -> None:
        """`listing=True` 才计入列表页数；详情页只计入总请求数。"""
        from ingestion.fetcher import Fetcher

        f = Fetcher(settings=SETTINGS, _sleep=lambda _s: None)
        f._requests_made = 5
        f._pages_fetched = 2
        assert f.requests_made == 5
        assert f.pages_fetched == 2


class TestConfigGuard:
    def test_delay_cannot_be_lowered(self) -> None:
        """环境变量/入参把延时调到 1 秒以下时，压回 1 秒。"""
        s = Settings(
            allowed_domains=("sgu.edu.cn",), request_delay_seconds=0.1, max_pages_per_run=3
        )
        assert s.request_delay_seconds >= 1.0

    def test_pages_cannot_exceed_cap(self) -> None:
        s = Settings(
            allowed_domains=("sgu.edu.cn",), request_delay_seconds=1.0, max_pages_per_run=9999
        )
        assert s.max_pages_per_run <= 50

    def test_env_cannot_widen_whitelist(self) -> None:
        """`SCRAPER_ALLOWED_DOMAINS` 只能在默认白名单内收窄，不能加新域。"""
        s = load_settings({"SCRAPER_ALLOWED_DOMAINS": "sgu.edu.cn,example.com"})
        assert "example.com" not in s.allowed_domains
        assert "sgu.edu.cn" in s.allowed_domains


class TestBlockedDetection:
    def test_403_is_blocked(self) -> None:
        assert looks_blocked(403, "<html>whatever</html>")

    def test_block_title_page(self) -> None:
        """实测拦截页：`<title>block</title>`，47KB，一个通知链接都没有。"""
        page = "<!doctype html><html><head><title>block</title></head><body>…</body></html>"
        assert looks_blocked(200, page)

    def test_normal_list_page_is_not_blocked(self) -> None:
        page = (
            "<html><head><title>教学通知</title></head><body>"
            '<ul><li><a href="info/1050/29459.htm">通知</a></li></ul></body></html>'
        )
        assert not looks_blocked(200, page)

    def test_blocked_error_is_distinct_type(self) -> None:
        """`BlockedError` 必须与 `FetchError` 分开——处置方式完全不同。"""
        assert not issubclass(BlockedError, SystemExit)


class TestDecode:
    def test_utf8(self) -> None:
        assert _decode("通知".encode(), "utf-8") == "通知"

    def test_gbk_fallback(self) -> None:
        assert _decode("通知".encode("gb18030"), "utf-8") == "通知"

    def test_bom_stripped(self) -> None:
        raw = "\ufeff通知".encode()
        assert _decode(raw, "utf-8") == "通知"


class TestAbsoluteUrl:
    def test_relative(self) -> None:
        assert (
            absolute_url("info/1050/29459.htm", "https://www.sgu.edu.cn/jwb/jxtz.htm")
            == "https://www.sgu.edu.cn/jwb/info/1050/29459.htm"
        )

    def test_root_relative(self) -> None:
        assert (
            absolute_url("/system/x.jsp", "https://www.sgu.edu.cn/jwb/jxtz.htm")
            == "https://www.sgu.edu.cn/system/x.jsp"
        )


class TestEnvAliases:
    """早期 .env 用的是 SCRAPER_DELAY_SECONDS / SCRAPER_MAX_PAGES。

    写错变量名必须**不静默失效** —— 否则你以为限了 3 页，实际抓了 50 页。
    """

    def test_plain_alias_for_delay(self) -> None:
        from ingestion.config import load_settings

        st = load_settings({"SCRAPER_DELAY_SECONDS": "2.5"})
        assert st.request_delay_seconds == 2.5

    def test_formal_name_wins(self) -> None:
        from ingestion.config import load_settings

        st = load_settings(
            {"SCRAPER_DELAY_SECONDS": "9", "SCRAPER_REQUEST_DELAY_SECONDS": "2"}
        )
        assert st.request_delay_seconds == 2.0

    def test_plain_alias_for_max_pages(self) -> None:
        from ingestion.config import load_settings

        st = load_settings({"SCRAPER_MAX_PAGES": "3"})
        assert st.max_pages_per_run == 3

    def test_alias_still_clamped(self) -> None:
        """别名走同一套压回逻辑，不能成为绕过闸门的后门。"""
        from ingestion.config import load_settings

        st = load_settings({"SCRAPER_DELAY_SECONDS": "0.1", "SCRAPER_MAX_PAGES": "999"})
        assert st.request_delay_seconds == 1.0
        assert st.max_pages_per_run == 50
