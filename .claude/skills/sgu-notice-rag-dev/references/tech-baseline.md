# tech-baseline.md — 基线与替换规则（只读引用）

> 只记录**选型 + 替换规则**，不重复论证。论证见 `docs/adr/` 与 `docs/prd.md`。
> 硬约束：**任何替换必须留下三样东西** —— 替换理由、引用基准数据或约束、对六人分工的影响。三者缺一，PR 打回。

## 1. 基线（v1，不得静默替换）

| 层 | 基线 | 版本 | 归属 | 关键依据 |
|---|---|---|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Element Plus + ECharts | Vue 3.5 / Vite 6 / EP 2.8 / ECharts 5.5 | D | 10,000 行列表虚拟滚动：首屏 1.68s → 74ms，最低 FPS 18 → 56，主线程阻塞 420ms → 18ms |
| 后端 API | Go + Gin | Go 1.23 / Gin 1.10 | C | 典型 QPS 12,000–30,000；并发 1000 时稳定 ~8,210 req/s |
| RAG 核心 | Python + LangChain/LangGraph + bge-m3 + bge-reranker | Py 3.12 / LC 0.3 | B | 切块/检索/评估生态在 Python 侧最成熟 |
| 向量库 | Qdrant | 1.17+ | B / F | 原生 dense+sparse 混合检索 + RRF（默认 k=2）；加权 RRF 1.17+ 可用 |
| 关系库 | PostgreSQL | 16 | C / F | notices / chunks / eval_runs |
| 缓存 | Redis | 7 | C / F | 令牌桶限流 + SSE session 计数 |
| 评估 | RAGAS | 0.2+ | E | Faithfulness 管生成、Context Precision 管检索，故障可分层定位 |
| 部署 | Docker Compose + Nginx | compose v2 / nginx 1.27 | F | 六人项目不需要 K8s |
| Mock | 自研静态 JSON + Vite 代理 | — | D | `VITE_USE_MOCK=true` 时零后端可跑 |

## 2. 允许的替换（已预批准，仍需在 PR 记录）

| 替换 | 条件 | 受影响成员 |
|---|---|---|
| Milvus 单机版替代 Qdrant | 需支持 sparse + RRF；Qdrant 不可用时 | B（召回实现）、F（compose） |
| Chroma 替代 Qdrant（**仅开发期**） | 不影响 `/internal/retrieve` 契约 | B、F |
| pgvector 替代 Qdrant | 数据量 < 5 万 chunk 时可行；须保留 sparse 或换 hybrid | B、F |
| FastAPI 替代 Go/Gin | **仅当团队 Go 经验为零**；接受 QPS 下降 | C、F |
| React + Ant Design 替代 Vue | D 全员熟悉 React 时 | D |
| RAGAS 指标名替换 | 不得改契约中的指标名，只允许内部计算方式调整 | E |

## 3. 替换记录模板（写进 PR 描述）

```
replace: <层> <旧> -> <新>
why: <一句话理由>
evidence: <基准数据 / 约束条件 / 硬性限制>
impact: A(<是否>) B(<...>) C D E F，预计 <n> 人日
contract: <是否改动 /internal/retrieve 或 /api/v1/*>  ← 若改，必须先走 SKILL.md §1
```

## 4. 不可替换（v1 冻结）

- `/internal/retrieve` 的请求/响应结构（`chunks[]` + `scores[]` 等长同序）。
- 时间过滤顺序（`SKILL.md §5` / `data-model.md §4`）。
- chunk 必填字段集合。
- `ALLOWED_DOMAINS` / `REQUEST_DELAY_SECONDS` / `MAX_PAGES_PER_RUN` 三个采集闸门的存在性。
