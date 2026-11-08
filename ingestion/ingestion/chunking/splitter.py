"""切块：把一段通知正文切成 320–800 token 的 chunk，并保留小节路径。

切块规则（A 的实现口径，与 SKILL.md §4 的 `section_path` 规则对齐）：

1. **按小标题切**：`一、` `（一）` `1.` 这类序号行是天然边界，优先在这里断开。
2. **超长小节再按句号切**，按目标长度累积，不切断句子。
3. **每个 chunk 的首行带上文档标题**（`title\\n{正文}`）——孤立召回时正文里的
   "第十六条"离开标题就没有上下文，带上标题能让 rerank 和答案抽取都更准。
4. `section_path` 记录"正文 > 一、报名安排"这样的层级，供答案里的"依据"行引用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 小标题：一、 （一） 1. 1、 第X条 —— 只用于切块边界，不做层级解析
_SECTION_RE = re.compile(
    r"^\s*(?:"
    r"[一二三四五六七八九十]+[、.．]"
    r"|（[一二三四五六七八九十]+）"
    r"|\([一二三四五六七八九十]+\)"
    r"|\d+[、.．]"
    r"|第[一二三四五六七八九十百]+[条章节]"
    r")\s*"
)

_SENTENCE_END = re.compile(r"(?<=[。；！？!?;])")

# 目标长度：中文按字符估算，1 汉字 ≈ 1 token（bge-m3 的中文分词接近字级）
CHUNK_TARGET = 500
CHUNK_MAX = 800
CHUNK_MIN = 120


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    section_path: str


def split_into_chunks(title: str, paragraphs: list[str]) -> list[ChunkDraft]:
    """正文段落 → chunk 草稿列表。每个 chunk 的 text 已带标题行。"""
    blocks = _split_blocks(paragraphs)
    merged = _merge_blocks(blocks)

    # CHUNK_MAX 约束的是**最终写进向量库的整段文本**，也就是带标题行的 text。
    # 标题也要占预算，所以正文的可用宽度要扣掉 `title + "\n"`。
    overhead = len(title) + 1 if title else 0
    body_budget = max(CHUNK_MIN, CHUNK_MAX - overhead)

    drafts: list[ChunkDraft] = []
    for section, body in merged:
        body = body.strip()
        if not body:
            continue
        for piece in _split_long(body, body_budget):
            text = f"{title}\n{piece}" if title else piece
            drafts.append(ChunkDraft(text=text, section_path=section))
    return drafts


def _split_blocks(paragraphs: list[str]) -> list[tuple[str, str]]:
    """把段落流按小标题切成 (section_path, body) 块。"""
    blocks: list[tuple[str, str]] = []
    current_section = "正文"
    buf: list[str] = []
    subsection = ""

    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if _SECTION_RE.match(p) and len(p) <= 40:
            # 小标题行：先把当前缓冲结算
            if buf:
                blocks.append((current_section, "\n".join(buf)))
                buf = []
            subsection = p
            current_section = f"正文 > {subsection}"
            buf.append(p)
        else:
            buf.append(p)

    if buf:
        blocks.append((current_section, "\n".join(buf)))
    return blocks


def _merge_blocks(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """相邻的短块合并到 CHUNK_TARGET 附近，避免产生大量 50 字的碎片。"""
    if not blocks:
        return []
    out: list[tuple[str, str]] = []
    sec, body = blocks[0]
    for nsec, nbody in blocks[1:]:
        if len(body) < CHUNK_MIN and nsec == sec:
            body = body + "\n" + nbody
        else:
            out.append((sec, body))
            sec, body = nsec, nbody
    out.append((sec, body))
    return out


def _split_long(body: str, limit: int = CHUNK_MAX) -> list[str]:
    """超过 `limit` 的块按句子重切。`limit` 由调用方按标题长度收窄。"""
    if len(body) <= limit:
        return [body]

    sentences = [s for s in _SENTENCE_END.split(body) if s.strip()]
    pieces: list[str] = []
    cur: list[str] = []
    cur_len = 0

    for s in sentences:
        if len(s) > limit:
            # 单句就超上限（"第X条"整段不换行的情况真实存在）：先把已攒的结算，
            # 再把这句硬切。硬切是最后手段，但不切就保证不了 CHUNK_MAX 这个硬约束。
            if cur:
                pieces.append("".join(cur).strip())
                cur, cur_len = [], 0
            pieces.append(s[:limit])
            rest_s = s[limit:]
            if rest_s:
                sentences_rest = rest_s
                cur = [sentences_rest]
                cur_len = len(sentences_rest)
            continue
        if cur_len + len(s) > CHUNK_TARGET and cur:
            pieces.append("".join(cur).strip())
            cur, cur_len = [], 0
        cur.append(s)
        cur_len += len(s)

    if cur:
        pieces.append("".join(cur).strip())

    # 尾巴太短就并回上一块，避免"半句话"chunk
    if len(pieces) >= 2 and len(pieces[-1]) < CHUNK_MIN:
        merged = pieces[-2] + pieces[-1]
        if len(merged) <= limit:
            pieces[-2] = merged
            pieces.pop()

    return [p for p in pieces if p]


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文按字，英文按 4 字符 1 token。用于 `token_count` 字段。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    rest = len(text) - cjk
    return cjk + max(1, rest // 4)
