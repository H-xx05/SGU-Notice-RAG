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
import threading
import time
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


# 加载锁：预热线程与"第一个用户请求"可能同时到达。lru_cache 在并发未命中时
# **会重复执行**被装饰的函数，两个线程各加载一份 2.3 GB 权重就是 OOM。
# 这里用双检锁保证只加载一次；两把锁共用一个，让 embedder 与 reranker 串行加载，
# 避免内存峰值翻倍。
_LOAD_LOCK = threading.Lock()

# 组件状态机：idle（还没开始）→ loading → ok / down。
# 只在真实模式下有意义；stub 模式恒为 ok。
_STATE: dict[str, str] = {"embedder": "idle", "reranker": "idle"}


class ModelUnavailable(RuntimeError):
    """模型未就绪（权重缺失或预热已失败）。

    必须显式抛，不能"再试一次"：`lru_cache` **不缓存异常**，加载失败时每个请求都会
    重新走一遍几十秒的加载，把一次故障放大成持续雪崩。预热已经判过死刑的，直接快速失败。
    """


@lru_cache(maxsize=1)
def _embedder_cached():
    from FlagEmbedding import BGEM3FlagModel  # 重量级依赖，延迟导入

    log.info("loading embedder %s (cache_dir=%s)", settings.embedding_model, settings.model_cache_dir)
    return BGEM3FlagModel(
        settings.embedding_model,
        use_fp16=settings.use_fp16,
        cache_dir=settings.model_cache_dir,
    )


@lru_cache(maxsize=1)
def _reranker_cached():
    from FlagEmbedding import FlagReranker

    log.info("loading reranker %s (cache_dir=%s)", settings.reranker_model, settings.model_cache_dir)
    return FlagReranker(
        settings.reranker_model,
        use_fp16=settings.use_fp16,
        cache_dir=settings.model_cache_dir,
    )


def _embedder():
    """取 embedder；未加载时加锁加载（双检）。"""
    if _STATE.get("embedder") == "down":
        raise ModelUnavailable(f"embedder 未就绪（{settings.embedding_model}），见 /internal/health")
    if _loaded(_embedder_cached):
        return _embedder_cached()
    with _LOAD_LOCK:
        return _embedder_cached()


def _reranker():
    """取 reranker；未加载时加锁加载（双检）。"""
    if _STATE.get("reranker") == "down":
        raise ModelUnavailable(f"reranker 未就绪（{settings.reranker_model}），见 /internal/health")
    if _loaded(_reranker_cached):
        return _reranker_cached()
    with _LOAD_LOCK:
        return _reranker_cached()


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
    """返回 `(embedder_ok, reranker_ok)` 的布尔形式（`components_state()` 的便捷包装）。

    仅用于"只想问一句行不行"的场合；`/internal/health` 需要区分 `loading`，用
    `components_state()`。
    """
    embedder_state, reranker_state = components_state()
    return embedder_state == "ok", reranker_state == "ok"


def _component_state(name: str, loaded_fn, model_id: str) -> str:
    """单组件三态：ok / loading / down。

    判定顺序有讲究：
    1. 已加载成功 → ok（最权威，不看别的信号）；
    2. 预热被标记为 down → down（失败过就不要再假装 loading）；
    3. 预热进行中 → loading；
    4. 还没开始预热（idle）→ 权重在本地算 loading（马上就会加载），不在则 down。
    """
    if _loaded(loaded_fn):
        return "ok"
    state = _STATE.get(name, "idle")
    if state == "down":
        return "down"
    if state == "loading":
        return "loading"
    return "loading" if model_available(model_id) else "down"


def components_state() -> tuple[str, str]:
    """`(embedder_state, reranker_state)`，直接喂给 `/internal/health`。

    stub 模式不加载任何模型，两个组件恒为 ok（与骨架期行为一致）。
    """
    if settings.rag_stub_mode:
        return "ok", "ok"
    return (
        _component_state("embedder", _embedder_cached, settings.embedding_model),
        _component_state("reranker", _reranker_cached, settings.reranker_model),
    )


def warmup() -> None:
    """把两个模型加载进内存，并各跑一次极短推理确认真的能用。

    为什么必须预热：C 侧 `RAG_TIMEOUT_SECONDS=8`，而 bge-m3 在 CPU 上首次加载要几十秒。
    不预热的话，**容器起来后第一个问题必然 50202 超时**——用户看到的是"服务坏了"，
    实际上只是还没热。

    为什么要跑一次推理而不只是加载：权重能读进来、但 tokenizer 缺文件或维度不匹配，
    这类问题只有真跑一次才会暴露。宁可启动时在日志里炸，也不要等用户提问时炸。

    失败不抛异常（本函数跑在后台线程里，抛了也没人接），只把状态置 down 并记日志；
    `/internal/health` 会因此返回 503，人和 compose 都能看见。
    """
    if settings.rag_stub_mode:
        return

    targets = (
        ("embedder", _embedder_cached, settings.embedding_model, _probe_embedder),
        ("reranker", _reranker_cached, settings.reranker_model, _probe_reranker),
    )
    for name, loaded_fn, model_id, probe in targets:
        if _loaded(loaded_fn):
            _STATE[name] = "ok"
            continue
        if not model_available(model_id):
            _STATE[name] = "down"
            log.error(
                "warmup %s skipped: %s 的权重不在 %s，先跑 model-puller",
                name,
                model_id,
                settings.model_cache_dir,
            )
            continue

        _STATE[name] = "loading"
        started = time.perf_counter()
        try:
            probe()
        except Exception:  # noqa: BLE001 - 后台线程里的任何异常都只降级，不抛出
            _STATE[name] = "down"
            log.exception("warmup %s failed after %.1fs", name, time.perf_counter() - started)
        else:
            _STATE[name] = "ok"
            log.info("warmup %s ok in %.1fs", name, time.perf_counter() - started)


def start_warmup() -> threading.Thread | None:
    """在后台线程里预热，**不阻塞启动**。

    必须是 daemon：容器收到 SIGTERM 时不能被一个正在加载 2.3 GB 权重的线程钉住，
    否则 compose 只能等 10s 后 SIGKILL，优雅退出形同虚设。
    """
    if settings.rag_stub_mode:
        return None
    thread = threading.Thread(target=warmup, name="model-warmup", daemon=True)
    thread.start()
    log.info("model warmup started in background (stub_mode=false)")
    return thread


def _probe_embedder() -> None:
    """最小可用性探测：一次稠密 + 一次稀疏，覆盖两条编码路径。"""
    _real_dense("预热")
    _real_sparse("预热")


def _probe_reranker() -> None:
    _real_rerank("预热", ["预热"])


class EmbeddingInfo:
    """给 /internal/health 上报当前生效的模型名（stub 模式要显式标注）。"""

    @staticmethod
    def describe() -> dict[str, str | bool]:
        return {
            "embedder": "stub-hash-1024" if settings.rag_stub_mode else settings.embedding_model,
            "reranker": "stub-overlap" if settings.rag_stub_mode else settings.reranker_model,
            "stub_mode": settings.rag_stub_mode,
        }
