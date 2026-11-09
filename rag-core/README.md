# rag-core（B 组 · 检索与时效性核心）

内部服务，只暴露 `POST /internal/retrieve` 与 `GET /internal/health`，**不对外**。
对外契约由 `api-server`（C 组）单点承载，详见 [docs/api.md](../docs/api.md) §6。

## 本地跑

```bash
cd rag-core
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -e ".[dev]"
pytest -q                                            # 66 个用例，全部离线可跑
uvicorn app.main:app --port 8000 --reload
```

`RAG_STUB_MODE=true`（默认）时不加载任何模型：向量来自确定性哈希，rerank 用词汇重合度。
这让整条链路可以做单元测试，且 CI 不需要 GPU 或模型下载。

## 接真实模型

```bash
pip install -r requirements-models.txt
docker compose --profile models up model-puller     # 预热 /models 卷
export RAG_STUB_MODE=false
```

模型文件不进镜像（约 4.5 GB），由 `model-puller` 服务挂载到 `/models`。
推理依赖（torch / transformers / FlagEmbedding）**要**进镜像——compose 里
`RAG_STUB_MODE` 的默认值就是 `false`，镜像里没有这些依赖会直接 ImportError。

冒烟（验收口径落成了一行命令）：

```bash
python -m app.tools.smoke_real
# 或指向任何一台已在跑的服务
python -m app.tools.smoke_real --base-url http://localhost:8000 --query "转专业报名截止时间"
```

它检查 `/internal/health` 三个组件是否都 `ok`、`/internal/retrieve` 顶层是否恰好 4 键、
chunk 是否恰好 13 键，并打印 top-N 与耗时。全 0 分或全命中同一条文档，在这里就能看出来。

### 真实模式的三条实现约定

| 约定 | 为什么 |
|---|---|
| 稠密向量归一化到 1024 维单位向量 | collection 是 Cosine；不归一化会让 dense/sparse 的尺度漂移，RRF 之后就不可解释 |
| 稀疏向量取 `lexical_weights`（token_id → 权重） | 中文专有名词（"转专业""学籍"）靠它精确命中，这是 bge-m3 相对纯稠密的核心价值 |
| rerank 分数一律 sigmoid 压到 `[0,1]` | cross-encoder 吐的是 logits（±10）。C 的 `MinAnswerScore=0.35` 按 [0,1] 标定，直接把 logits 递上去等于删掉了拒答 |

### health 为什么不做推理探活

`/internal/health` 的超时是 5s，而 bge-m3 首次加载要几十秒、首次下载要 2.3 GB。
在健康检查里干重活，compose 会把容器当成"起来了但没就绪"反复重启，永远绿不了。
所以 health 只判断 **权重是否已在本地**（HF 缓存布局或本地目录），真正加载失败留给
第一次 `/internal/retrieve` 暴露。`embedder` 与 `reranker` 分开上报，避免"半个模型就绪"显示成全绿。

## 目录

| 文件 | 职责 |
| --- | --- |
| `app/schemas.py` | **与 C 的唯一交界**。改字段必须走契约流程。 |
| `app/time_filter.py` | 时效判定的唯一实现（纯函数，无时钟、无 IO）。 |
| `app/retriever.py` | 硬过滤 → 双路召回 → RRF → 精排 → 降权 → 截断。 |
| `app/embedder.py` | bge-m3 / reranker 封装 + stub 实现 + 模型就绪探测。 |
| `app/store.py` | Qdrant 访问（`query_points` + payload filter 翻译） + `FixtureStore` 内存兜底。 |
| `app/timeline.py` | 时区与"今天"的唯一来源。 |
| `app/tools/smoke_real.py` | 真实模式冒烟（health 三 ok + 契约形状）。 |
| `tests/fixtures/chunks.jsonl` | 12 条贴近真实的切片，含跨年度同题两条版本。 |

## 三条不可改的行为

1. **`time_filter.judge()` 是全系统唯一的时效判定**。E 组评估调它，C 组**不**判时效（只用 B 返回的 `is_current`/`recency_bucket` 做拒答决策）。
2. **长期有效（`effective_to = null`）必须永远可见**。payload filter 里的 `is_empty` 分支专门为此存在，删掉它会让《学籍管理规定》这类现行文件全部消失。
3. **降权发生在精排之后、截断之前**。先按相关性排，再按时效打折——过期文档仍然可见，只是不占首位。
