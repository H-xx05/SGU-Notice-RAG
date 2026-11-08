"""时区与"今天"的唯一来源。

整个系统只允许从这里取当前日期——包括 A 的采集、B 的兜底、C 的 meta.as_of。
避免 go/python/浏览器三处各自用 `datetime.now()` 导致跨日边界出现"今天是哪天"的分歧。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .settings import settings

log = logging.getLogger(__name__)

# Asia/Shanghai 无夏令时，固定 +08:00 与 tzdata 版本无关。
# 开发机（尤其 Windows）常常没有 tzdata，此时退回固定偏移，避免整个服务起不来。
_CN_STD = timezone(timedelta(hours=8), name="CST")


def _load_tz():
    try:
        return ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):  # pragma: no cover - 环境相关
        log.warning(
            "timezone %r not found (tzdata missing?), falling back to UTC+08:00",
            settings.timezone,
        )
        return _CN_STD


_TZ = _load_tz()


def now() -> datetime:
    return datetime.now(tz=_TZ)


def today() -> date:
    return now().date()


def today_iso() -> str:
    return today().isoformat()


def parse_iso_date(value: str) -> date | None:
    """解析 YYYY-MM-DD；非法值返回 None（调用方按 unknown 处理，不猜）。"""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None
