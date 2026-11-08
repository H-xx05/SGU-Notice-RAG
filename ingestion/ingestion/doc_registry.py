"""文档标识与版本关系：`doc_id` / `doc_version` / `topic_key` / `supersedes_doc_id`。

四个字段的取值口径直接决定"时效性"能否判对，因此集中在这里，一处可审计：

| 字段 | 规则 | 为什么这样选 |
|---|---|---|
| `doc_id` | `jwb-jxtz-{发布年}-{站点 news_id}` | 用站点自带 id，**重跑幂等**；不按抓取顺序自增，否则补抓一次全网 id 全漂移 |
| `doc_version` | `v{发布年}.{同年同主题内的第 n 个版本}` | 同年改一次发文就是 .2；跨年天然由年份比大小 |
| `doc_version_rank` | `年*100 + n` | 单整数可比，Qdrant/Postgres 都能直接排序 |
| `topic_key` | 标题去掉年份/学年/虚词后的核心词 | 让 2025 版和 2026 版落到同一个 key 上，才能谈"谁替代谁" |
| `supersedes_doc_id` | **仅当新版正文显式写"废止/替代"** 且同 topic_key 且 rank 更大 | 标题相似 ≠ 替代关系；猜错会把现行规定误标为过期 |

SKILL.md §4 与 docs/data-model.md §4 都写了"禁止用标题相似度直接判定 is_current=false"，
`link_versions()` 就是那条禁令的代码实现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 标题里不影响主题的部分：年份、学年、届次、虚词
_NOISE = [
    re.compile(r"关于"),
    re.compile(r"做好|开展|进行|组织|报送|印发|转发"),
    re.compile(r"\d{4}\s*[-—－]?\s*\d{0,4}\s*学年"),
    re.compile(r"\d{4}\s*年(?:度)?"),
    re.compile(r"第[一二三四五六七八九十\d]+(?:届|次|批|期)"),
    re.compile(r"[（(][^）)]*[）)]"),  # 括号补充说明
    re.compile(r"的?通知$|的?公告$|的?公示$|的?通告$"),
    re.compile(r"工作$"),
]

# 显式替代/废止表述——只有命中这里才允许建立 supersedes 关系
_SUPERSEDE_HINT = re.compile(
    r"(?:废止|失效|停止执行|予以废止|自动废止|取代|替代|同时废止|原.{0,20}(?:不再执行|停止执行))"
)


@dataclass
class DocMeta:
    """一个文档在入库前算出的全部标识字段。"""

    doc_id: str
    doc_version: str
    doc_version_rank: int
    topic_key: str
    publish_date: str
    supersedes_doc_id: str | None = None
    is_current: bool = True


def year_of(publish_date: str) -> int:
    return int(publish_date[:4]) if publish_date[:4].isdigit() else 1970


def make_doc_id(publish_year: int, news_id: str) -> str:
    """`jwb-jxtz-2026-29459`。

    契约写"序号4位"；本站 news_id 是 5 位（29459），所以宽度按实际位数走，
    只保证 **≥4 位且定长**。用站点 id 而非自增序号，是为了重跑时 id 不变。
    """
    return f"jwb-jxtz-{publish_year}-{news_id}"


def topic_key_of(title: str) -> str:
    """标题 → 主题键。同一事项的不同年份版本得到同一个 key。"""
    t = title.strip()
    for pat in _NOISE:
        t = pat.sub("", t)
    t = re.sub(r"[《》「」【】\s、，,。.：:；;！!？?（）()\-—_/]+", "", t)
    return t or title.strip()


def mentions_supersede(text: str) -> bool:
    return bool(_SUPERSEDE_HINT.search(text or ""))


def assign_versions(docs: list[tuple[str, str, str]]) -> list[DocMeta]:
    """按发布年 + 同主题顺序分配 doc_version。

    入参：`[(news_id, title, publish_date), …]`，顺序不重要（内部会排序）。
    出参：与入参**同序**的 `DocMeta` 列表。
    """
    metas: list[DocMeta] = []
    per_topic_year: dict[tuple[str, int], int] = {}

    # 排序键：先按日期，再按 news_id（数字）——同一天的两篇也有稳定顺序
    order = sorted(
        range(len(docs)),
        key=lambda i: (docs[i][2], int(docs[i][0]) if docs[i][0].isdigit() else 0),
    )

    for i in order:
        news_id, title, publish_date = docs[i]
        py = year_of(publish_date)
        topic = topic_key_of(title)
        n = per_topic_year.get((topic, py), 0) + 1
        per_topic_year[(topic, py)] = n
        rank = py * 100 + n
        metas.append((i, DocMeta(
            doc_id=make_doc_id(py, news_id),
            doc_version=f"v{py}.{n}",
            doc_version_rank=rank,
            topic_key=topic,
            publish_date=publish_date,
        )))

    out: list[DocMeta] = [m for _, m in sorted(metas, key=lambda x: x[0])]
    return out


def link_versions(metas: list[DocMeta], texts: dict[str, str] | list[str]) -> None:
    """就地写入 `supersedes_doc_id` 与 `is_current`。**唯一**的版本链实现。

    判定链（三条同时满足才置旧版 `is_current=False`）：
      1. 同一个 `topic_key`；
      2. 存在 `doc_version_rank` 更大的文档；
      3. **更新那篇的正文显式写了废止/替代**（`mentions_supersede`）。

    缺任何一条都保持 `is_current=True` —— 宁可多留一份旧文，不可错杀现行规定。

    `texts` 可以是 `{doc_id: 正文}` 映射（推荐，不受列表顺序影响），也可以是与
    `metas` 等长的列表。**按 doc_id 取文而不是按列表下标**，这样调用方打乱顺序
    也不会把废止声明算到别人头上。
    """
    by_topic: dict[str, list[int]] = {}
    for i, m in enumerate(metas):
        by_topic.setdefault(m.topic_key, []).append(i)

    for idxs in by_topic.values():
        if len(idxs) < 2:
            continue
        ordered = sorted(idxs, key=lambda i: metas[i].doc_version_rank)
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            if not mentions_supersede(_text_of(metas, texts, cur)):
                continue  # 新版没说废止 → 不动
            metas[prev].is_current = False
            # 指向**紧邻**的上一版：连续三版时 3→2、2→1，整条链可回溯
            metas[cur].supersedes_doc_id = metas[prev].doc_id


def _text_of(metas: list[DocMeta], texts: dict[str, str] | list[str], i: int) -> str:
    if isinstance(texts, dict):
        return texts.get(metas[i].doc_id, "")
    if 0 <= i < len(texts):
        return texts[i]
    return ""
