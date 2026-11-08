"""Embedding / Rerank 的薄封装。

骨架期（RAG_STUB_MODE=true）用**确定性哈希向量**：同一段文本永远得到同一个向量，
因此检索排序可复现，CI 才能断言"某条 query 的 top1 是哪条 chunk"。
真实模型在 M1 之后接进来，只需把 `_real_*` 补完，调用方代码不动。

为什么是哈希而不是随机：随机向量会让测试变成掷骰子；哈希向量保留了"同一文本→同一向量"
这一关键性质，足以验证整条链路的正确性（过滤、排序、降权、拒答）。
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

from .settings import settings

DENSE_DIM = 1024

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """朴素切词：英文按词、中文按字。真实环境用 bge-m3 的分词器，骨架期够用且可复现。"""
    return _TOKEN_RE.findall(text.lower())


def _seed(token: str) -> bytes:
    return hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()


def dense_embed(text: str) -> list[float]:
    """确定性稠密向量：每个 token 散列到若干维度，最后 L2 归一化。"""
    if settings.rag_stub_mode:
        return _stub_dense(text)
    return _real_dense(text)


def _stub_dense(text: str) -> list[float]:
    vec = [0.0] * DENSE_DIM
    for tok in tokenize(text):
        h = _seed(tok)
        for i in range(2):  # 每个 token 落两个维度，减少碰撞带来的偏差
            idx = int.from_bytes(h[i * 4 : i * 4 + 4], "little") % DENSE_DIM
            sign = 1.0 if h[i + 6] & 1 else -1.0
            vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def sparse_embed(text: str) -> tuple[list[int], list[float]]:
    """稀疏向量（Qdrant sparse vector 格式：indices + values）。

    stub 与真实的 BM25 在**形态**上一致，因此 RRF 融合逻辑可以完整跑通。
    """
    counts: dict[str, int] = {}
    for tok in tokenize(text):
        counts[tok] = counts.get(tok, 0) + 1
    indices: list[int] = []
    values: list[float] = []
    for tok, tf in sorted(counts.items()):
        idx = int.from_bytes(_seed(tok)[:4], "little") % (2**31 - 1)
        indices.append(idx)
        values.append(1.0 + math.log(tf))
    return indices, values


@lru_cache(maxsize=1)
def _embedder():
    """真实模型懒加载：只有 RAG_STUB_MODE=false 才会走到这里。"""
    from FlagEmbedding import BGEM3FlagModel  # 重量级依赖，延迟导入

    return BGEM3FlagModel(
        settings.embedding_model,
        use_fp16=settings.use_fp16,
        cache_dir=settings.model_cache_dir,
    )


def _real_dense(text: str) -> list[float]:
    out = _embedder().encode([text], return_dense=True, return_sparse=False, return_colbert_vecs=False)
    return out["dense_vecs"][0].tolist()


@lru_cache(maxsize=1)
def _reranker():
    from FlagEmbedding import FlagReranker

    return FlagReranker(
        settings.reranker_model,
        use_fp16=settings.use_fp16,
        cache_dir=settings.model_cache_dir,
    )


def rerank(query: str, docs: list[str]) -> list[float]:
    """Cross-encoder 精排打分；stub 模式下用 query-doc token 重叠度模拟。"""
    if not docs:
        return []
    if settings.rag_stub_mode:
        return [_stub_rerank_score(query, d) for d in docs]
    return [float(s) for s in _reranker().compute_score([[query, d] for d in docs])]


def _stub_rerank_score(query: str, doc: str) -> float:
    """重合度打分：既是确定性函数，又真的能奖励"命中关键词"的文档。"""
    q = set(tokenize(query))
    if not q:
        return 0.0
    d = set(tokenize(doc))
    hit = len(q & d)
    return hit / math.sqrt(len(q) * max(len(d), 1))


class EmbeddingInfo:
    """给 /internal/health 上报当前生效的模型名（stub 模式要显式标注）。"""

    @staticmethod
    def describe() -> dict[str, str | bool]:
        return {
            "embedder": "stub-hash-1024" if settings.rag_stub_mode else settings.embedding_model,
            "reranker": "stub-overlap" if settings.rag_stub_mode else settings.reranker_model,
            "stub_mode": settings.rag_stub_mode,
        }
