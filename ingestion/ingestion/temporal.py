"""时效性字段抽取：从正文里读 `effective_from` / `effective_to` / `precision`。

**这是整个项目"时效性幻觉"问题的第一道防线**，也是最容易出错的地方。三条硬规则：

1. **只认显式表述**，不做推理。"本学期""按校历"→ `precision="term"` + 区间给不出来时
   宁可 `null`，也不要猜一个日期填进去。
2. **未知必须显式表达**：`{from: None, to: None, precision: "unknown"}`，禁止 `1970-01-01`
   之类的哨兵值（见 SKILL.md §4「规则」）。
3. **落款日期 ≠ 生效日期**。落款是"发文那天"，很多通知写完就生效，但也有很多写
   "自 2026 年 9 月 1 日起执行"。所以 `effective_from` **只能**来自 §1 的显式句式；
   落款日期只作为 `publish_date` 的补充来源（在 detail_page 里已处理）。

从真实通知里核实到的句式（2026-11 抓取样本）：

    本规定自2024年6月1日起施行。
    本通知自发布之日起执行。
    请于11月6日（星期五）上午12:00前将…
    报名截止时间为4月10日17:00，逾期不再受理。
    有效期至2026年12月31日。
    于2026年3月4日至2026年5月31日期间受理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 2026年9月1日 / 2026-09-01 / 2026.9.1
_CN_DATE = r"(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"
_ISO_DATE = r"(?P<y2>\d{4})-(?P<m2>\d{1,2})-(?P<d2>\d{1,2})"
# 只有年月：2026年9月
_CN_MONTH = r"(?P<y3>\d{4})\s*年\s*(?P<m3>\d{1,2})\s*月"

_FROM_PATTERNS = [
    # 自2024年6月1日起施行 / 自2024年6月1日起执行 / 自2024年6月1日起实施
    re.compile(rf"自\s*{_CN_DATE}\s*起\s*(?:施行|执行|实施|生效|适用|开始)"),
    # 2024年6月1日起施行（无"自"）
    re.compile(rf"{_CN_DATE}\s*起\s*(?:施行|执行|实施|生效|适用)"),
    # 自2026-09-01起执行
    re.compile(rf"自\s*{_ISO_DATE}\s*起"),
    # 本办法自发布之日起执行（相对日期，单独处理）
]

_FROM_RELATIVE = re.compile(r"自\s*(?:本(?:通知|办法|规定|细则|方案|意见)?\s*)?发布之日\s*起")

# 截止类表述的真实形态（2026-11 抓取样本）：
#   报名截止时间为2026年4月10日17:00，逾期不再受理。
#   受理截止日期为2026年4月10日。
#   截止到2026年12月31日 / 有效期至2026年12月31日
_TO_PREFIX = (
    r"(?:有效期\s*(?:至|到)"
    r"|(?:截止|截至)\s*(?:时间|日期|日)?\s*(?:为|是)?"
    r"|截止(?:到|于)"
    r"|至)"
)
_TO_PATTERNS = [
    re.compile(rf"{_TO_PREFIX}\s*{_CN_DATE}\s*(?:止|结束)?"),
    re.compile(rf"{_TO_PREFIX}\s*{_ISO_DATE}\s*(?:止)?"),
]

# 区间：2026年3月4日至2026年5月31日 / 2026年3月4日—2026年5月31日
# 注意：区间模式里日期出现两次，不能用带命名组的 _CN_DATE（会触发
# "redefinition of group name"），所以这里用无名组，日期由 _all_dates() 兜底解析。
_CN_DATE_PLAIN = r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
_ISO_DATE_PLAIN = r"\d{4}-\d{1,2}-\d{1,2}"
_SEP = r"(?:至|到|—|－|-|~|～)"
_RANGE_PATTERNS = [
    re.compile(rf"{_CN_DATE_PLAIN}\s*{_SEP}\s*{_CN_DATE_PLAIN}"),
    re.compile(rf"{_ISO_DATE_PLAIN}\s*{_SEP}\s*{_ISO_DATE_PLAIN}"),
]

# "本学期""本学年""按校历" —— 有时效范围但无法给出精确日期
_TERM_HINTS = re.compile(r"(?:本学期|本学年|当学期|该学期|按校历|按学校校历|学年内)")

# 明确表示"长期有效"的表述
_LONG_TERM = re.compile(r"(?:长期有效|自发布之日起长期|无固定有效期)")


@dataclass(frozen=True)
class EffectivePeriod:
    """契约要求的 `effective_period` 对象（SKILL.md §4）。"""

    from_: str | None = None
    to: str | None = None
    precision: str = "unknown"  # day | month | term | unknown

    def as_dict(self) -> dict[str, object]:
        return {"from": self.from_, "to": self.to, "precision": self.precision}


UNKNOWN = EffectivePeriod(None, None, "unknown")


def extract(text: str, publish_date: str | None = None) -> EffectivePeriod:
    """从正文抽取生效区间。**只在有显式表述时才返回非 unknown**。"""
    if not text:
        return UNKNOWN

    # (1) 显式区间最高优先
    for pat in _RANGE_PATTERNS:
        m = pat.search(text)
        if m:
            groups = m.groupdict()
            start = _mk(groups.get("y"), groups.get("m"), groups.get("d")) or _mk(
                groups.get("y2"), groups.get("m2"), groups.get("d2")
            )
            # 区间模式的第二组日期在不同正则里位置不同：用 finditer 逐个日期取
            dates = _all_dates(m.group(0))
            if len(dates) >= 2:
                start, end = dates[0], dates[1]
                if start and end and start <= end:
                    return EffectivePeriod(start, end, "day")

    # (2) 起 + 止 分别匹配
    start = _match_from(text, publish_date)
    end = _match_to(text)

    if start and end:
        return EffectivePeriod(start, end, "day")
    if start:
        return EffectivePeriod(start, None, "day")
    if end:
        # 只有截止日：说明这是个有期限的事项，起点未知
        return EffectivePeriod(None, end, "day")

    # (3) 只有年月/学期：给到精度，不给具体日
    if _TERM_HINTS.search(text):
        return EffectivePeriod(None, None, "term")
    month = re.search(_CN_MONTH, text)
    if month:
        return EffectivePeriod(None, None, "month")

    # (4) 长期有效
    if _LONG_TERM.search(text):
        return EffectivePeriod(None, None, "day")

    return UNKNOWN


def _match_from(text: str, publish_date: str | None) -> str | None:
    for pat in _FROM_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        g = m.groupdict()
        d = _mk(g.get("y"), g.get("m"), g.get("d")) or _mk(g.get("y2"), g.get("m2"), g.get("d2"))
        if d:
            return d
    # "自发布之日起执行" → 起点 = 发布日期（这是**显式**的相对表述，不算猜）
    if _FROM_RELATIVE.search(text) and publish_date:
        return publish_date
    return None


def _match_to(text: str) -> str | None:
    for pat in _TO_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        g = m.groupdict()
        d = _mk(g.get("y"), g.get("m"), g.get("d")) or _mk(g.get("y2"), g.get("m2"), g.get("d2"))
        if d:
            return d
    return None


def _mk(y: str | None, m: str | None, d: str | None) -> str | None:
    if not (y and m and d):
        return None
    try:
        yi, mi, di = int(y), int(m), int(d)
    except ValueError:
        return None
    if not (1 <= mi <= 12 and 1 <= di <= 31):
        return None
    return f"{yi:04d}-{mi:02d}-{di:02d}"


def _all_dates(s: str) -> list[str]:
    out: list[str] = []
    for pat in (_CN_DATE, _ISO_DATE):
        for m in re.finditer(pat, s):
            g = m.groupdict()
            d = _mk(g.get("y"), g.get("m"), g.get("d")) or _mk(g.get("y2"), g.get("m2"), g.get("d2"))
            if d:
                out.append(d)
    return out
