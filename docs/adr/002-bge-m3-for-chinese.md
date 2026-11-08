# ADR-002：中文 embedding 选择 bge-m3，配合 RRF 混合检索与 Rerank

- 状态：Accepted
- 日期：2026-11-08
- 决策者：B（RAG 核心）、E（评估）
- 相关：`docs/data-model.md §3`、`references/tech-baseline.md`

## 背景

语料是**中文教务行政文书**，有三个特点：

1. **长**：单条通知正文 1,000–8,000 字，切块后单块 320–800 tokens，需要模型能吃长上下文（避免切得太碎丢语义）。
2. **术语密集且相似**：`转专业` / `转专业报名` / `转专业工作安排` / `退役大学生士兵转专业` 在同一页共存，纯 dense 检索容易把这些"近义但不同事项"混为一谈。
3. **数字与日期是答案本体**：`4月10日17:00`、`2026年`、`大三`。这类 token 必须能被**字面**匹配到，不能只靠语义向量。

第 2、3 点直接决定了"只用 dense"是不够的：需要 **dense + sparse 混合**，并且需要**重排**把"语义像但时间错"的候选压下去。

## 决策

1. 嵌入模型：**`BAAI/bge-m3`**（dense 1024 维 + 自带 sparse lexical weights）。
2. 检索：Qdrant 原生 **dense + sparse 双路召回，RRF(k=2) 融合**。
3. 重排：**`BAAI/bge-reranker-v2-m3`**，top20 → top8。
4. 时间过滤在召回**之前**（见 `data-model.md §3`），不放在 rerank 之后。

## 依据

| 维度 | bge-m3 | text-embedding-3-large（API） | m3e-base | jina-embeddings-v3 |
|---|---|---|---|---|
| 中文行政文书表现 | 强（MTEB-zh 第一梯队） | 强 | 中 | 中上 |
| 最长输入 | **8,192 tokens** | 8,191 | 512 | 8,192 |
| dense + sparse 一体 | **是** | 否（仅 dense） | 否 | 部分（需 extra 参数） |
| 私有化部署 | **可以，本地推理** | 否（必须出网，含校内数据） | 是 | 是（权重需申请） |
| 成本 | 一次性下载 ~2.3 GB，推理吃 GPU/CPU | 按 token 计费 + 数据出校 | 低 | 低 |
| 与 Qdrant 集成 | 官方文档级支持 | 需自建 hybrid | 需自拼 sparse | 需自拼 sparse |

**决定性约束**：教务通知是**校内数据**，不应上传第三方 API。这一条直接排除 text-embedding-3-large，剩下的候选里只有 bge-m3 同时满足"长上下文 + dense/sparse 一体 + 可私有化"。

**为什么需要 sparse**：`4月10日`、`2026年`、`缓考` 这类词，sparse 的精确匹配能力是 dense 的补充。消融实验（E 负责，样本 40 题）显示：仅 dense 时 `context_recall` 明显低于混合检索——具体数字以 E 的回归报告为准，不在此预先断言。

**为什么需要 rerank**：RRF 融合分只看排名不看语义深度；bge-reranker 用 cross-encoder 对 (query, chunk) 逐对打分，能把"标题相似但事项不同"的候选打下去。尤其对 `转专业` vs `转专业报名` 这种细粒度区分有效。

## 部署约束

| 约束 | 值 | 影响 |
|---|---|---|
| 模型体积 | ~2.3 GB（bge-m3）+ ~2.2 GB（reranker） | 镜像大，需在 `docker compose --profile models` 预热 |
| 推理内存 | bge-m3 ≈ 2.5 GB（fp16，CPU） | 建议 ≥8 GB；不够时用 `RAG_STUB_MODE=true` 跑通链路 |
| 冷启动 | 30–90s（CPU） | `rag-core` 健康检查 `start_period: 120s` |
| 首次推理延迟 | 1–3s（CPU） | 预热：启动时跑一次 dummy encode |
| GPU（可选） | CUDA 12 + fp16 | 单卡 T4 可把 encode 降到 ~50ms |
| 离线 | 必须预下载权重 | 无网环境启动即失败，除非 stub 模式 |

**评估依据**（E 的口径）：模型替换必须提供同 40 题数据集上的 `context_recall` / `context_precision` / `stale_recall_rate` 三项前后对比；仅凭"感觉更好"不得替换。

## 后果

- 正向：中文长文本 + 混合检索一步到位；无需第三方 API；数据不出校。
- 负向：镜像/内存较大；CPU 推理慢，演示机需要预热；rerank 增加约 150–300ms 延迟（已计入 p99 < 2s 预算）。
- 风险：若演示机内存 <8 GB，需降级为 `bge-small-zh` + 关闭 rerank，并**记录在评估报告中**（指标会下降，属于已知降级）。

## 替代方案评估

| 方案 | 否决理由 |
|---|---|
| OpenAI text-embedding-3-large | 数据出校（校内通知），且不支持 sparse |
| m3e-base | 512 上下文太短，长通知必须切碎，丢上下文 |
| jina-embeddings-v3 | 权重授权需申请，团队时间不够 |
| 仅 dense + 不做 rerank | 无法区分"近义不同事项"，`stale_recall_rate` 与 `context_precision` 均受损 |
| 用 LLM 做 embedding | 延迟与成本不可接受 |
