"""时效性判定的唯一实现（SKILL.md §5 第 1、2、7 步）。

设计原则：
1. **纯函数**：不读库、不读 env、不看时钟（`as_of` 由外面传进来）。这样 B 与 E 用的是
   同一份判定逻辑，评估结论才对得上线上行为。
2. **失败即 unknown**：日期缺失/非法一律判 unknown，绝不"默认当现行"——那是幻觉的第一来源。
3. **边界闭合**：effective_to 当天仍然有效（学校通知普遍"某日止"含当天）。

判定口径（docs/data-model.md §5）：
    as_of < effective_from                 -> not_yet（尚未生效，等同无效）
    effective_from <= as_of <= effective_to -> current
    as_of > effective_to                   -> stale
    任一日期缺失/非法                        -> unknown
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from .schemas import TimeFilterBody
from .timeline import parse_iso_date, today

# 判定结果的三态。注意它**不在**内部 HTTP 契约里（Chunk 只带 is_current），
# 而是 B 内部与 E 侧共用的口径；C 侧另有自己的 recencyBucket（client/mapping.go）。
RecencyBucket = Literal["current", "stale", "unknown"]

# Qdrant payload 里用于 range 过滤的字段名（A 侧写入时拆开，见 docs/data-model.md §3）。
PAYLOAD_EFFECTIVE_FROM = "effective_from"
PAYLOAD_EFFECTIVE_TO = "effective_to"


@dataclass(frozen=True, slots=True)
class TimeDecision:
    """单条 chunk 的时效判定结果。"""

    bucket: RecencyBucket
    effective_from: date | None
    effective_to: date | None

    @property
    def is_current(self) -> bool:
        return self.bucket == "current"

    @property
    def is_stale(self) -> bool:
        return self.bucket == "stale"

    @property
    def penalty_factor(self) -> float:
        """过期护栏的降权系数（SKILL.md §5 第 6 步）。"""
        return 0.5 if self.bucket == "stale" else 1.0


def judge(
    *,
    effective_from: str | None,
    effective_to: str | None,
    publish_date: str | None = None,
    as_of: date | None = None,
) -> TimeDecision:
    """判定一条 chunk 在 as_of 当天是否有效。

    publish_date 只在**完全没有有效期信息**时作为退路参与判定：
    通知没写有效期时，"发布即长期有效"是学校的惯例，但仍需 as_of >= publish_date。
    """
    ref = as_of or today()
    start = parse_iso_date(effective_from) if effective_from else None
    end = parse_iso_date(effective_to) if effective_to else None

    # 字段有值但解析不出来 = 数据脏，不能猜。直接 unknown，让上游去修数据。
    if (effective_from and start is None) or (effective_to and end is None):
        return TimeDecision("unknown", None, None)

    if start is None and end is None:
        # 没有有效期字段：退化为"发布日期之后即有效"。
        published = parse_iso_date(publish_date) if publish_date else None
        if published is None:
            return TimeDecision("unknown", None, None)
        if ref < published:
            return TimeDecision("unknown", None, None)
        return TimeDecision("current", None, None)

    if start is not None and ref < start:
        return TimeDecision("stale", start, end)  # 尚未生效 = 现在不能用
    if end is not None and ref > end:
        return TimeDecision("stale", start, end)
    return TimeDecision("current", start, end)


def resolve_window(
    tf: TimeFilterBody, as_of: date | None = None
) -> tuple[date, date] | None:
    """把 TimeFilter 解析成 Qdrant range 过滤用的闭区间；None 表示不加硬过滤。

    mode 语义（docs/api.md §2.2）：
      auto  —— 默认。不加时间区间，靠 is_current payload + 过期护栏；as_of 当"今天"。
      range —— 用户显式指定区间，**硬过滤**掉区间外的 chunk（"我要查 2024 年的规定"）。
      off   —— 不做时效过滤（仅用于 E 的对照实验与调试，前端不暴露）。
    """
    if tf.mode == "off":
        return None

    if tf.mode == "range":
        start = parse_iso_date(tf.date_from) if tf.date_from else None
        end = parse_iso_date(tf.date_to) if tf.date_to else None
        if start is None or end is None:
            # 校验发生在 C 侧（返回 40001）；走到这里说明调用方越过了 C，按不退让处理。
            raise ValueError("time_filter.mode=range 需要合法的 date_from 与 date_to")
        if start > end:
            raise ValueError("date_from 不能晚于 date_to")
        return (start, end)

    # mode == "auto"
    return None


def range_payload_filter(window: tuple[date, date] | None) -> dict | None:
    """把闭区间转成 Qdrant payload filter 片段。

    注意 Qdrant 的 range 是数值/字符串比较，这里用 **区间重叠** 判断：
    一条 chunk 只要它的 [effective_from, effective_to] 与查询窗口有交集就保留。
    没有 effective_from 视为 -inf，没有 effective_to 视为 +inf（长期有效）。

    长期有效性必须用 `is_empty` 单独兜住：range 的 gte 对 null 字段不成立，
    若只写 `effective_to >= start`，那条 2024 年的学籍管理规定会被错误过滤掉。
    """
    if window is None:
        return None

    start, end = window
    return {
        "must": [
            {"key": PAYLOAD_EFFECTIVE_FROM, "range": {"lte": end.isoformat()}},
        ],
        # 至少满足其一：截止日在窗口之后，或压根没有截止日（长期有效）
        "should": [
            {"key": PAYLOAD_EFFECTIVE_TO, "range": {"gte": start.isoformat()}},
            {"is_empty": {"key": PAYLOAD_EFFECTIVE_TO}},
        ],
    }


def current_payload_filter(as_of: date | None = None) -> dict:
    """默认的"现行有效"硬过滤：effective_from <= as_of 且 (无 effective_to 或 effective_to >= as_of)。

    这个 filter 在 Qdrant 侧先砍掉大部分过期通知，是**性能与准度的双重保险**；
    `should` 里的 is_empty 分支保证长期有效的规定（如学籍管理规定）不会被漏掉。
    """
    ref = (as_of or today()).isoformat()
    return {
        "must": [
            {"key": PAYLOAD_EFFECTIVE_FROM, "range": {"lte": ref}},
        ],
        "should": [
            {"key": PAYLOAD_EFFECTIVE_TO, "range": {"gte": ref}},
            {"is_empty": {"key": PAYLOAD_EFFECTIVE_TO}},
        ],
    }
