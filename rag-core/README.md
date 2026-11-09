# rag-core（B 组 · 检索与时效性核心）

内部服务，只暴露 `POST /internal/retrieve` 与 `GET /internal/health`，**不对外**。
对外契约由 `api-server`（C 组）单点承载，详见 [docs/api.md](../docs/api.md) §6。

## 本地跑

```bash
cd rag-core
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -e ".[dev]"
pytest -q                                            # 43 个用例，全部离线可跑
uvicorn app.main:app --port 8000 --reload
```

`RAG_STUB_MODE=true`（默认）时不加载任何模型：向量来自确定性哈希，rerank 用词汇重合度。
这让整条链路可以做单元测试，且 CI 不需要 GPU 或模型下载。

## 接真实模型

真实检索要**两样东西**，缺一个都起不来：

| 要什么 | 怎么给 | 为什么 |
|---|---|---|
| 依赖库（torch + FlagEmbedding） | 构建参数 `WITH_MODELS=1` | 默认镜像**不含**它们，`import FlagEmbedding` 会直接 ImportError |
| 权重（bge-m3 + reranker，约 2.3 GB） | `model-puller` 下到 `model-cache` 卷 | 权重不进镜像，避免每次改代码重下 |

```bash
# 容器里（推荐）
WITH_MODELS=1 docker compose build rag-core model-puller
WITH_MODELS=1 docker compose --profile models run --rm model-puller
# 然后在 .env 里 RAG_STUB_MODE=false，重启 rag-core

# 本地裸机
pip install -r requirements-models.txt
docker compose --profile models run --rm model-puller
export RAG_STUB_MODE=false
```

> **`WITH_MODELS=0` 的镜像里跑 `model-puller` 会明确报错退出 1**，不会假装成功。
> 这是刻意的：一句"模型预下载完成"配上空的缓存目录，比直接报错难查得多。

`WITH_MODELS` 是**构建期**参数（`ARG`），不是运行期环境变量——改它必须重新 build。
`rag-core` 与 `model-puller` 必须用同一个值，否则会出现"模型下好了但 import 不了"。

## 目录

| 文件 | 职责 |
| --- | --- |
| `app/schemas.py` | **与 C 的唯一交界**。改字段必须走契约流程。 |
| `app/time_filter.py` | 时效判定的唯一实现（纯函数，无时钟、无 IO）。 |
| `app/retriever.py` | 硬过滤 → 双路召回 → RRF → 精排 → 降权 → 截断。 |
| `app/embedder.py` | bge-m3 / reranker 封装 + stub 实现。 |
| `app/store.py` | Qdrant 访问 + `FixtureStore` 内存兜底。 |
| `app/timeline.py` | 时区与"今天"的唯一来源。 |
| `tests/fixtures/chunks.jsonl` | 13 条贴近真实的切片，含跨年度同题两条版本。 |

## 三条不可改的行为

1. **`time_filter.judge()` 是全系统唯一的时效判定**。E 组评估调它，C 组**不**判时效（只用 B 返回的 `is_current`/`recency_bucket` 做拒答决策）。
2. **长期有效（`effective_to = null`）必须永远可见**。payload filter 里的 `is_empty` 分支专门为此存在，删掉它会让《学籍管理规定》这类现行文件全部消失。
3. **降权发生在精排之后、截断之前**。先按相关性排，再按时效打折——过期文档仍然可见，只是不占首位。
