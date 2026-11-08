"""用真实站点片段（2026-11 抓取）做解析回归。

样本不是编的：`LIST_SAMPLE` 与 `DETAIL_SAMPLE` 都来自
`https://www.sgu.edu.cn/jwb/jxtz.htm` 与 `info/1050/29459.htm` 的真实响应，
只截取结构相关的部分。

这两段 HTML 里各埋着一个坑：
- 列表页分页是**倒序**的（第 2 页 = `jxtz/96.htm`）；
- 详情页正文的汉字被逐字拆进带 `letter-spacing` 的多个 `<span>` 里。
测试就是防止有人"优化"解析器时把这两点改坏。
"""

from __future__ import annotations

from ingestion.parsers.detail_page import parse_detail_page, sign_date_of
from ingestion.parsers.list_page import find_last_page_url, parse_list_page

LIST_SAMPLE = """
<div class="list fl">
<script language="javascript" src="/system/resource/js/ajax.js"></script>    <UL>
            <li>

            <a href="info/1050/29459.htm" target="_blank" title="关于做好韶关学院第六届学位评定分委员会成员调整工作的通知">关于做好韶关学院第六届学位评定分委员会成员调整工作的通知</a>
        
<span class="date">2026-11-02</span>

</li>
  

        <li>

            <a href="info/1050/29449.htm" target="_blank" title="关于征求《韶关学院学分认定和转换管理办法（第二次征求意见稿）》意见的通知">关于征求《韶关学院学分认定和转换管理办法（第二次征求意见稿）》意见的通知</a>
        
<span class="date">2026-10-27</span>

</li>
  

    </UL>
</br>

  <link rel="stylesheet" Content-type="text/css" href="/system/resource/css/pagedown/sys.css"><div class="pb_sys_common pb_sys_normal pb_sys_style2" style="margin-top:10px;text-align:center;"><span class="p_t">共1452条</span> <span class="p_pages"><span class="p_first_d p_fun_d">首页</span><span class="p_prev_d p_fun_d">上页</span><span class="p_no_d">1</span><span class="p_no"><a href="jxtz/96.htm">2</a></span><span class="p_no"><a href="jxtz/95.htm">3</a></span><span class="p_dot">...</span><span class="p_no"><a href="jxtz/1.htm">97</a></span><span class="p_next p_fun"><a href="jxtz/96.htm">下页</a></span><span class="p_last p_fun"><a href="jxtz/1.htm">尾页</a></span></span></div>
</div>
"""

DETAIL_SAMPLE = """
<div class="conth">
<form name="_newscontent_fromname">
<div class="conth1">关于做好韶关学院第六届学位评定分委员会成员调整工作的通知</div>

<div class="conthsj">作者：学籍管理科&nbsp;&nbsp;时间：2026-11-02&nbsp;&nbsp;</div>
    <div id="vsb_content" class="concon"><div class="v_news_content">
<p><span style="font-family:方正仿宋_gb2312"><span>校内各部门、二级学院：</span></span></p>
<p><span style="font-family:方正仿宋_gb2312"><span>&nbsp;一、学位评定分委员会委员构成</span></span></p>
<p><span style="font-family:方正仿宋_gb2312"><span>&nbsp;（一）学位评定分委员会由</span><span>5至9人组成，设主席1人，副主席1人。</span></span></p>
<p><span style="font-family:方正仿宋_gb2312"><span>（五）学位评定分委员会成员人数为单数，任期四年。</span></span></p>
<p><span style="font-family:方正仿宋_gb2312"><span>二、各学位评定分委员会主要职责</span></span></p>
<p><span style="font-family:方正仿宋_gb2312"><span>（一）审定所辖学科专业研究生培养方案、研究生学位授予标准；</span></span></p>
<p><span style="font-family:方正仿宋_gb2312"><span>请各单位于</span></span><span><span>11</span></span><span><span>月</span></span><span><span>6</span></span><span><span>日（星期</span></span><span><span>五</span></span><span><span>）上午12:00</span></span><span><span>前将附件</span><span>1纸质盖章版交至校学位办（厚德楼203室）。</span></span></p>
<p style="text-align:center"><span><span>&nbsp;校学位评定委员会办公室（教务部）</span></span></p>
<p style="text-align:center"><span><span><span>2026年11月2日</span></span></span></p>
</div></div><div id="div_vote_id"></div>
    
    <div class="sxt">
        <UL style="list-style-type:none;"> 
            <li>附件【<a href="/system/_content/download.jsp?urltype=news.DownloadAttachUrl&owner=2038170832&wbfileid=64DF46862CC7C81507A68C2198A45D1E" target="_blank">附件1：韶关学院第六届二级学院学位评定分委会委员推荐名单.xlsx</a>】已下载<span id="nattach16825585">0</span>次</li>
        </UL>
    </div>

</form>
</div>
"""

PAGE_URL = "https://www.sgu.edu.cn/jwb/jxtz.htm"


class TestListPage:
    def test_items(self) -> None:
        page = parse_list_page(LIST_SAMPLE, PAGE_URL)
        assert len(page.items) == 2
        first = page.items[0]
        assert first.news_id == "29459"
        assert first.publish_date == "2026-11-02"
        assert first.title.startswith("关于做好韶关学院第六届学位评定分委员会")
        assert first.url == "https://www.sgu.edu.cn/jwb/info/1050/29459.htm"

    def test_total_count(self) -> None:
        assert parse_list_page(LIST_SAMPLE, PAGE_URL).total_count == 1452

    def test_next_page_is_descending(self) -> None:
        """分页倒序：第 2 页是 jxtz/96.htm。解析器必须**读链接**而不是拼 page+1。"""
        page = parse_list_page(LIST_SAMPLE, PAGE_URL)
        assert page.next_url == "https://www.sgu.edu.cn/jwb/jxtz/96.htm"

    def test_last_page(self) -> None:
        assert find_last_page_url(LIST_SAMPLE, PAGE_URL) == "https://www.sgu.edu.cn/jwb/jxtz/1.htm"

    def test_no_next_on_last_page(self) -> None:
        """最后一页 `<span class="p_next_d">下页</span>`（带 _d，且没有 `<a>`）。"""
        last = LIST_SAMPLE.replace(
            '<span class="p_next p_fun"><a href="jxtz/96.htm">下页</a></span>',
            '<span class="p_next_d p_fun_d">下页</span>',
        )
        assert parse_list_page(last, PAGE_URL).next_url is None

    def test_ignores_non_detail_links(self) -> None:
        """`jxtz/96.htm` 这类分页链接不是通知，不能被当成 item。"""
        page = parse_list_page(LIST_SAMPLE, PAGE_URL)
        assert all("/info/1050/" in it.url for it in page.items)


class TestDetailPage:
    def test_title_and_date(self) -> None:
        d = parse_detail_page(DETAIL_SAMPLE, "29459", "https://www.sgu.edu.cn/jwb/info/1050/29459.htm")
        assert d.title == "关于做好韶关学院第六届学位评定分委员会成员调整工作的通知"
        assert d.publish_date == "2026-11-02"

    def test_paragraphs_rejoin_split_spans(self) -> None:
        """正文汉字被拆进多个 span，取文本内容时必须拼回来。"""
        d = parse_detail_page(DETAIL_SAMPLE, "29459", "https://x")
        joined = d.text
        assert "校内各部门、二级学院：" in joined
        assert "学位评定分委员会由5至9人组成" in joined
        assert "请各单位于11月6日（星期五）上午12:00前" in joined

    def test_no_script_or_nbsp_garbage(self) -> None:
        d = parse_detail_page(DETAIL_SAMPLE, "29459", "https://x")
        for p in d.paragraphs:
            assert "\u00a0" not in p
            assert "<" not in p

    def test_attachment(self) -> None:
        d = parse_detail_page(DETAIL_SAMPLE, "29459", "https://x")
        assert len(d.attachments) == 1
        a = d.attachments[0]
        assert a.name == "附件1：韶关学院第六届二级学院学位评定分委会委员推荐名单.xlsx"
        # 相对链接要补成绝对地址
        assert a.url.startswith("https://x/system/_content/download.jsp")

    def test_missing_title_does_not_crash(self) -> None:
        d = parse_detail_page("<html><body>nothing</body></html>", "1", "https://x")
        assert d.title == ""
        assert d.paragraphs == []


class TestSignDate:
    def test_last_date_wins(self) -> None:
        """落款在文末，正文中间的引用日期不算。"""
        text = "依据2024年6月1日发布的文件…\n…\n2026年11月2日"
        assert sign_date_of(text) == "2026-11-02"

    def test_none_when_absent(self) -> None:
        assert sign_date_of("没有任何日期") is None

    def test_invalid_month(self) -> None:
        assert sign_date_of("2026年13月40日") is None
