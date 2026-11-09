"""Embedding / Rerank 的薄封装。

两种模式，同一份调用方代码：

- **stub（`RAG_STUB_MODE=true`）**：确定性哈希向量 + 词汇重合度打分。同一段文本永远得到
  同一个向量，因此排序可复现，CI 才能断言"某条 query 的 top1 是哪条 chunk"，也不需要 GPU。
- **真实（`RAG_STUB_MODE=false`）**：bge-m3 出稠密 + 稀疏两路，bge-reranker-v2-m3 做精排。

真实模式的三个约定（改动会影响 E 的评估口径与 C 的拒答阈值，动之前先看 §5）：

1. **稠密向量必须归一化到 1024 维单位向量**。collection 是 Cosine，未归一化也能算，
   但 dense/sparse 两路分数的相对尺度会漂，RRF 之后就不可解释了。
2. **稀疏向量取 bge-m3 的 `lexical_weights`**（token_id → 权重），丢弃 0 权重。
   这正是 bge-m3 相对"纯稠密"的价值所在：中文专有名词（"转专业""学籍"）靠它精确命中。
3. **精排分数一律压到 `[0, 1]`**。cross-encoder 输出的是 logits（可正可负、量级到 ±10），
   而 C 侧 `MinAnswerScore=0.35` 是按 [0,1] 标定的拒答阈值。直接把 logits 递上去，
   阈值就形同虚设——所有正分都"高于 0.35"，该拒的全都不拒。这里统一过 sigmoid。

为什么 rerank 不用 `compute_score(normalize=True)` 交给库做 sigmoid：该参数的默认值随
FlagEmbedding 版本变过（1.3.x 是 None → 跟随构造参数）。本项目锁 1.3.3 虽然够用，
但"分数尺度"是会被 E 拿去做前后对比的口径，自己算一次 sigmoid 成本为零、行为可测。
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from functools import lru_cache
from pathlib import Path

from .settings import settings

log = logging.getLogger(__name__)

DENSE_DIM = 1024

# Qdrant collection 里的命名向量名（infra/scripts/init-qdrant.sh 建的就是这两个）。
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

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

    stub 与真实的 bge-m3 在**形态**上一致，因此 RRF 融合逻辑可以完整跑通。
    """
    if settings.rag_stub_mode:
        return _stub_sparse(text)
    return _real_sparse(text)


def _stub_sparse(text: str) -> tuple[list[int], list[float]]:
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


# --------------------------------------------------------------------- 真实模型加载


@lru_cache(maxsize=1)
def _embedder():
    """真实模型懒加载：只有 RAG_STUB_MODE=false 才会走到这里。"""
    from FlagEmbedding import BGEM3FlagModel  # 重量级依赖，延迟导入

    log.info("loading embedder %s (cache_dir=%s)", settings.embedding_model, settings.model_cache_dir)
    return BGEM3FlagModel(
        settings.embedding_model,
        use_fp16=settings.use_fp16,
        cache_dir=settings.model_cache_dir,
    )


@lru_cache(maxsize=1)
def _reranker():
    from FlagEmbedding import FlagReranker

    log.info("loading reranker %s (cache_dir=%s)", settings.reranker_model, settings.model_cache_dir)
    return FlagReranker(
        settings.reranker_model,
        use_fp16=settings.use_fp16,
        cache_dir=settings.model_cache_dir,
    )


def _real_dense(text: str) -> list[float]:
    out = _embedder().encode(
        [text], return_dense=True, return_sparse=False, return_colbert_vecs=False
    )
    vec = [float(v) for v in out["dense_vecs"][0]]

    if len(vec) != DENSE_DIM:
        # collection 建的是 1024 维；换了模型却没重建 collection 会静默乱排，必须吵。
        raise ValueError(
            f"稠密向量维度 {len(vec)} 与 collection 的 {DENSE_DIM} 不一致，"
            f"检查 EMBED_MODEL（当前 {settings.embedding_model}）或重建 collection"
        )

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _real_sparse(text: str) -> tuple[list[int], list[float]]:
    """bge-m3 的 lexical_weights → Qdrant sparse 向量。

    `lexical_weights` 的键是 **token_id 的字符串形式**，值是该 token 的权重（通常是 IDF 修正
    后的词频）。Qdrant 的 sparse indices 必须是正整数（`uint32`），因此：

    - 非数字键（分词器特殊 token 偶尔会漏进来）直接丢；
    - 权重 ≤ 0 的项丢掉——留着只会让 RRF 里多一条 0 分候选；
    - indices 升序去重，方便人工比对与复现。
    """
    out = _embedder().encode(
        [text], return_dense=False, return_sparse=True, return_colbert_vecs=False
    )
    weights = out["lexical_weights"][0] or {}

    pairs: dict[int, float] = {}
    for tok, w in weights.items():
        try:
            idx = int(tok)
        except (TypeError, ValueError):
            continue
        try:
            value = float(w)
        except (TypeError, ValueError):
            continue
        if idx <= 0 or value <= 0:
            continue
        pairs[idx] = value

    indices = sorted(pairs)
    return indices, [pairs[i] for i in indices]


def rerank(query: str, docs: list[str]) -> list[float]:
    """Cross-encoder 精排打分，返回值域固定 `[0, 1]`；stub 模式下用词汇重合度模拟。"""
    if not docs:
        return []
    if settings.rag_stub_mode:
        return [_stub_rerank_score(query, d) for d in docs]
    return _real_rerank(query, docs)


def _real_rerank(query: str, docs: list[str]) -> list[float]:
    raw = _reranker().compute_score([[query, d] for d in docs], normalize=False)
    if isinstance(raw, int | float):  # 单条输入时部分版本会直接返回标量
        raw = [raw]
    return [_sigmoid(float(s)) for s in raw]


def _sigmoid(x: float) -> float:
    """数值稳定版 sigmoid：`0.5 * (1 + tanh(x/2))`，避免 `exp(-x)` 在 x 很负时溢出。"""
    return 0.5 * (1.0 + math.tanh(x / 2.0))


def _stub_rerank_score(query: str, doc: str) -> float:
    """重合度打分：既是确定性函数，又真的能奖励"命中关键词"的文档。"""
    q = set(tokenize(query))
    if not q:
        return 0.0
    d = set(tokenize(doc))
    hit = len(q & d)
    return hit / math.sqrt(len(q) * max(len(d), 1))


# ------------------------------------------------------------------------- 就绪探测


def _loaded(model_fn) -> bool:
    info = getattr(model_fn, "cache_info", None)
    return bool(info and info().currsize > 0)


def model_available(model_id: str) -> bool:
    """模型权重是否已在本地就绪（不加载、不下载、不联网）。

    为什么 health 不做真正的推理探活：`/internal/health` 的 HTTP 超时是 5s，而 bge-m3
    首次加载要几十秒、首次下载要 2.3 GB。在健康检查里干重活，compose 会把容器当成
    "起来了但没就绪"反复重启，反而永远绿不了。所以这里只判断"权重在不在这儿"，
    真正的加载失败留给第一次 `/internal/retrieve` 暴露（并写日志）。

    查找顺序：模型 id 本身就是本地目录 → 直接用；否则按 HuggingFace Hub 的缓存布局
    `{MODEL_CACHE_DIR}/models--{org}--{name}/snapshots/*/config.json` 找。
    """
    local = Path(model_id)
    if local.is_dir():
        return (local / "config.json").exists()

    org, _, name = model_id.partition("/")
    snapshot_root = Path(settings.model_cache_dir) / f"models--{org}--{name}" / "snapshots"
    if not snapshot_root.is_dir():
        return False
    return any((d / "config.json").exists() for d in snapshot_root.iterdir() if d.is_dir())


def models_ready() -> tuple[bool, bool]:
    """返回 `(embedder_ok, reranker_ok)`，供 `/internal/health` 如实上报。

    stub 模式下不加载任何模型，两个组件都算就绪（等价于骨架期的行为）。
    """
    if settings.rag_stub_mode:
        return True, True
    embedder_ok = _loaded(_embedder) or model_available(settings.embedding_model)
    reranker_ok = _loaded(_reranker) or model_available(settings.reranker_model)
    return embedder_ok, reranker_ok


class EmbeddingInfo:
    """给 /internal/health 上报当前生效的模型名（stub 模式要显式标注）。"""

    @staticmethod
    def describe() -> dict[str, str | bool]:
        return {
            "embedder": "stub-hash-1024" if settings.rag_stub_mode else settings.embedding_model,
            "reranker": "stub-overlap" if settings.rag_stub_mode else settings.reranker_model,
            "stub_mode": settings.rag_stub_mode,
        }
