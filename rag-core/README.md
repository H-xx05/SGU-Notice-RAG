# rag-core（B 组 · 检索与时效性核心）

内部服务，只暴露 `POST /internal/retrieve` 与 `GET /internal/health`，**不对外**。
对外契约由 `api-server`（C 组）单点承载，详见 [docs/api.md](../docs/api.md) §6。

## 本地跑

```bash
cd rag-core
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -e ".[dev]"
pytest -q                                            # 36 个用例，全部离线可跑
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

## 目录

| 文件 | 职责 |
| --- | --- |
| `app/schemas.py` | **与 C 的唯一交界**。改字段必须走契约流程。 |
| `app/time_filter.py` | 时效判定的唯一实现（纯函数，无时钟、无 IO）。 |
| `app/retriever.py` | 硬过滤 → 双路召回 → RRF → 精排 → 降权 → 截断。 |
| `app/embedder.py` | bge-m3 / reranker 封装 + stub 实现。 |
| `app/store.py` | Qdrant 访问 + `FixtureStore` 内存兜底。 |
| `app/timeline.py` | 时区与"今天"的唯一来源。 |
| `tests/fixtures/chunks.jsonl` | 12 条贴近真实的切片，含跨年度同题两条版本。 |

## 三条不可改的行为

1. **`time_filter.judge()` 是全系统唯一的时效判定**。E 组评估调它，C 组**不**判时效（只用 B 返回的 `is_current`/`recency_bucket` 做拒答决策）。
2. **长期有效（`effective_to = null`）必须永远可见**。payload filter 里的 `is_empty` 分支专门为此存在，删掉它会让《学籍管理规定》这类现行文件全部消失。
3. **降权发生在精排之后、截断之前**。先按相关性排，再按时效打折——过期文档仍然可见，只是不占首位。
