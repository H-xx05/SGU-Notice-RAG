"""采集配置：四道合规闸门 + 站点常量。

`ALLOWED_DOMAINS` / `REQUEST_DELAY_SECONDS` / `MAX_PAGES_PER_RUN` 是元提示词规定的
**硬参数**，写死在代码里作为下限，环境变量只能让它们更保守（不能更激进）：

| 参数 | 代码默认 | 环境变量能做什么 |
|---|---|---|
| `ALLOWED_DOMAINS` | `("sgu.edu.cn",)` | 只能**缩减**：环境变量是子集才生效 |
| `REQUEST_DELAY_SECONDS` | `1.0` | 只能**调大**：小于 1.0 一律按 1.0 处理 |
| `MAX_PAGES_PER_RUN` | `50` | 只能**调小**：大于 50 一律按 50 处理。只算**列表页** |
| `MAX_REQUESTS_PER_RUN` | `800` | 只能**调小**：大于 2000 按 2000 处理。算**所有请求**（含详情页与重试） |

这样即使有人 `export MAX_PAGES_PER_RUN=9999`，也抓不出整站。

**为什么是"压回"而不是"报错"**：报错会让一个错误的环境变量直接阻断开采（A 的第一天就可能
卡在启动失败上，而失败原因藏在 40 行日志里）；压回则保证"无论怎么配，行为都在合规区间内"，
同时 `cli` 会打印实际生效值，人一眼能看出自己被压了。合规约束不该依赖使用者的自觉。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- 硬下限

DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = ("sgu.edu.cn",)
DEFAULT_REQUEST_DELAY_SECONDS: float = 1.0
DEFAULT_MAX_PAGES_PER_RUN: int = 50
# 总请求数上限：列表页 + 详情页 + 重试全部计入。50 页 × 15 条/页 已经远超一次
# 增量采集的需要（首次全量请分多次跑），所以 800 是一个很宽松的防呆值。
MAX_REQUESTS_HARD_CAP: int = 2000
DEFAULT_MAX_REQUESTS_PER_RUN: int = 800

# 列表页 15 条/页；`共{N}条` 与页脚链接互相印证，见 parsers/list_page.py
ITEMS_PER_PAGE: int = 15

# --------------------------------------------------------------------------- 站点常量

BASE_URL = "https://www.sgu.edu.cn/jwb/"
LIST_PATH = "jxtz.htm"
# 详情页统一形如 info/1050/{news_id}.htm，1050 是"教学通知"栏目的频道号
DETAIL_URL_TEMPLATE = "https://www.sgu.edu.cn/jwb/info/1050/{news_id}.htm"
COLUMN_ID = "1050"

USER_AGENT = (
    "SGU-Notice-RAG/0.1 (+course project; contact: jwb-notice-rag@example.edu.cn) "
    "python-urllib"
)

# 单次请求超时（秒）。详情页正文最长约 40KB，10s 足够；超时视为失败并重试 1 次。
REQUEST_TIMEOUT_SECONDS: float = 10.0
MAX_RETRIES: int = 1


# --------------------------------------------------------------------------- 配置对象


@dataclass(frozen=True)
class Settings:
    """运行期配置。所有字段都有保守默认值，构造函数负责把"更激进"的输入压回去。"""

    allowed_domains: tuple[str, ...] = DEFAULT_ALLOWED_DOMAINS
    request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS
    max_pages_per_run: int = DEFAULT_MAX_PAGES_PER_RUN
    max_requests_per_run: int = DEFAULT_MAX_REQUESTS_PER_RUN
    data_dir: Path = field(default_factory=lambda: Path("data"))
    user_agent: str = USER_AGENT

    def __post_init__(self) -> None:
        # 延迟不允许低于 1s（合规底线，见 README「采集合规」）
        if self.request_delay_seconds < DEFAULT_REQUEST_DELAY_SECONDS:
            object.__setattr__(self, "request_delay_seconds", DEFAULT_REQUEST_DELAY_SECONDS)
        # 单次页数不允许高于 50（不递归整站）
        if self.max_pages_per_run > DEFAULT_MAX_PAGES_PER_RUN:
            object.__setattr__(self, "max_pages_per_run", DEFAULT_MAX_PAGES_PER_RUN)
        if self.max_pages_per_run < 1:
            object.__setattr__(self, "max_pages_per_run", 1)
        # 总请求数同样只能往小里调
        if self.max_requests_per_run > MAX_REQUESTS_HARD_CAP:
            object.__setattr__(self, "max_requests_per_run", MAX_REQUESTS_HARD_CAP)
        if self.max_requests_per_run < 1:
            object.__setattr__(self, "max_requests_per_run", 1)
        if not self.allowed_domains:
            object.__setattr__(self, "allowed_domains", DEFAULT_ALLOWED_DOMAINS)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """从环境变量读配置。允许的变量：

    - `SCRAPER_ALLOWED_DOMAINS`（逗号分隔，必须是默认白名单的子集）
    - `SCRAPER_REQUEST_DELAY_SECONDS`（别名 `SCRAPER_DELAY_SECONDS`）
    - `SCRAPER_MAX_PAGES_PER_RUN`（列表页翻页数；别名 `SCRAPER_MAX_PAGES`）
    - `SCRAPER_MAX_REQUESTS_PER_RUN`（总请求数，含详情页）
    - `INGESTION_DATA_DIR`

    别名是为了兼容早期 `.env`：变量名写错时**不能静默失效** —— 那意味着你以为
    限制了 3 页，实际在抓 50 页。所以两种拼法都读，正式名优先。
    """
    e = os.environ if env is None else env

    def pick(*names: str) -> str:
        for n in names:
            v = e.get(n)
            if v:
                return v.strip()
        return ""

    domains = DEFAULT_ALLOWED_DOMAINS
    raw_domains = pick("SCRAPER_ALLOWED_DOMAINS")
    if raw_domains:
        wanted = tuple(d.strip().lower() for d in raw_domains.split(",") if d.strip())
        # 只接受默认白名单的子集：防止通过环境变量扩大抓取范围
        subset = tuple(d for d in wanted if any(d == a or d.endswith("." + a) for a in DEFAULT_ALLOWED_DOMAINS))
        domains = subset or DEFAULT_ALLOWED_DOMAINS

    delay = DEFAULT_REQUEST_DELAY_SECONDS
    try:
        delay = float(pick("SCRAPER_REQUEST_DELAY_SECONDS", "SCRAPER_DELAY_SECONDS") or DEFAULT_REQUEST_DELAY_SECONDS)
    except ValueError:
        delay = DEFAULT_REQUEST_DELAY_SECONDS

    max_pages = DEFAULT_MAX_PAGES_PER_RUN
    try:
        max_pages = int(
            pick("SCRAPER_MAX_PAGES_PER_RUN", "SCRAPER_MAX_PAGES") or DEFAULT_MAX_PAGES_PER_RUN
        )
    except ValueError:
        max_pages = DEFAULT_MAX_PAGES_PER_RUN

    max_requests = DEFAULT_MAX_REQUESTS_PER_RUN
    try:
        max_requests = int(
            pick("SCRAPER_MAX_REQUESTS_PER_RUN") or DEFAULT_MAX_REQUESTS_PER_RUN
        )
    except ValueError:
        max_requests = DEFAULT_MAX_REQUESTS_PER_RUN

    data_dir = Path(pick("INGESTION_DATA_DIR") or "data")

    return Settings(
        allowed_domains=domains,
        request_delay_seconds=delay,
        max_pages_per_run=max_pages,
        max_requests_per_run=max_requests,
        data_dir=data_dir,
    )
