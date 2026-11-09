---
name: sgu-notice-rag-dev
description: Guides development of SGU-Notice-RAG (韶关学院教务处教学通知时效性 RAG). Use when adding or modifying API handlers, frontend request functions, chunk metadata, retrieval/filter logic, ingestion parsers, or evaluation queries in this repository, and whenever a change may affect the frozen API contract, the chunk schema, the six-person ownership boundary, or the time-filter pipeline. Enforces the frozen contract, the metadata schema, and the composable service topology so A–F can develop six links in parallel without integration drift.
---

# SGU-Notice-RAG 开发一致性 Skill

本 Skill 是**契约的唯一入口**。它不介绍项目背景（背景见 `docs/prd.md`），只回答一个问题：
**"我现在要改的东西，允不允许改，改完必须同步什么。"**

加载顺序：本文件 → 按需读 `references/api-contract.md`、`references/data-model.md`、`references/tech-baseline.md`。
references 是**摘要**，不是全文；完整文档在 `docs/`，但**代码与 `docs/` 冲突时以 `references` 为准**（理由见 §1）。

| 我要做的事 | 先读 |
|---|---|
| 加/改 REST 接口、字段、错误码 | §3 + `references/api-contract.md` |
| 改 chunk 字段、时间过滤、切块 | §4 §5 + `references/data-model.md` |
| 换技术选型（框架/向量库/模型） | §1 + `references/tech-baseline.md` |
| 改 compose 服务、端口 | §2 §6 |
| 跑回归、改评估口径 | §5 + `eval/README.md` |

---

## 1. 契约优先级声明（CONTRACT FIRST）

1. **`references/api-contract.md` 是冻结契约**。任何代码与该文件不一致时，**代码是错的**，不是契约是错的。
2. **契约变更流程（三步，缺一不可）**：
   - 同时取得 **C（Go API）与 D（前端）** 的 PR review 同意；
   - 在同一个人 PR 内更新 `docs/api.md` **和** `references/api-contract.md`（两者字段必须逐字一致）；
   - PR 描述以 `contract-change:` 开头，写明「旧字段 → 新字段」「谁需要改」「是否向后兼容」。
3. **允许不改契约的变更**：新增**可选**请求字段（默认值保持旧行为）、新增**可选**响应字段、新增错误码（旧错误码语义不变）、内部实现重写。
4. **禁止的变更**：修改已有字段名或类型、删除字段、改变字段语义、改变路径、改变 HTTP 方法、把可选字段改成必填。
5. **第 1 周冻结**：`v1.0.0-contract-freeze` tag 之后，前端 D 的 Mock 与后端 C 的 Handler 必须能直接对接，不允许"先合了再说"。
6. 校验脚本：`bash .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh` —— 检查前端请求路径是否都出现在契约里、契约是否被改动而 Skill 未同步。**PR 前必跑**。

---

## 2. 目录归属（OWNERSHIP）

一人一目录，**跨目录修改必须在 PR 描述里 @ 出涉及的成员**。

| 目录 | 成员 | 允许放什么 | 不允许放什么 |
|---|---|---|---|
| `ingestion/` | **A** 采集与 ETL | 列表/详情解析、切块、元数据注入、`chunks.jsonl` 产出 | 向量化、检索、对外 HTTP |
| `rag-core/` | **B** 检索与 RAG 核心 | 嵌入、混合召回、RRF、Rerank、时间过滤、拒答编排、`/internal/*` | 对外 `/api/v1/*`、JWT、Redis 限流 |
| `api-server/` | **C** Go 高并发 API | 对外 REST、SSE、JWT、限流、内部客户端封装 | 直接连 Qdrant、直接算 embedding |
| `frontend/` | **D** Vue 3 前端 | 页面、虚拟滚动、SSE 组件、ECharts、`src/api/` | 硬编码后端地址、自造字段名 |
| `eval/` | **E** 评估体系 | `queries.jsonl`、RAGAS 跑批、回归报告 | 改业务代码 |
| `infra/` | **F** 基础设施 | compose、CI、Nginx、健康检查脚本 | 改别人目录里的业务逻辑 |
| `docs/` | 全体 | 文档 | 除契约变更流程外，不得单方面改 `api.md` |
| `.claude/skills/` | 全体 | 本 Skill 与 references | 与 `docs/api.md` 不一致的内容 |

- 共享代码（约 60 行的响应包、时间解析）**复制而不是抽公共库**，避免第 1 周就产生跨人依赖。
- 新文件找不到归属时，放本链路目录下的 `internal/` 或 `_shared/`，不要新建顶层目录。

---

## 3. 接口冻结清单（FROZEN INTERFACES）

字段名不可变。新增字段必须**可选**且向后兼容（Go 用 `omitempty`，TS 用 `?:`，Python 用默认值）。

### 3.1 对外 REST（Go API，前缀 `/api/v1`）

| 路径 | 方法 | 鉴权 | 归属 |
|---|---|---|---|
| `/api/v1/auth/token` | POST | 公开（**仅 dev**，生产置 403） | C |
| `/api/v1/notices` | GET | 公开 | C（数据来自 Postgres） |
| `/api/v1/chat` | POST | JWT | C → 调 B |
| `/api/v1/chat/stream` | GET | JWT（`?token=` 兜底） | C → 调 B |
| `/api/v1/eval/summary` | GET | JWT | C（数据来自 Postgres，E 写入） |
| `/healthz` | GET | 公开 | C |

统一响应包（**所有**非 SSE 响应）：

```json
{ "code": 0, "message": "ok", "data": { } }
```

### 3.2 `GET /api/v1/notices`

Query：`page`=1、`page_size`=20（≤100）、`year`、`q`、`publish_from`、`publish_to`、`content_type`、`doc_version`。

`data`：

```json
{
  "items": [{
    "doc_id": "jwb-jxtz-2026-29459",
    "title": "关于2026年转专业工作的通知",
    "publish_date": "2026-03-04",
    "effective_from": "2026-03-04",
    "effective_to": "2026-05-31",
    "doc_version": "v2026.1",
    "is_current": true,
    "supersedes_doc_id": "jwb-jxtz-2025-29001",
    "content_type": "notice_body",
    "source_url": "https://www.sgu.edu.cn/jwb/info/1050/29459.htm"
  }],
  "total": 2, "page": 1, "page_size": 20
}
```

### 3.3 `POST /api/v1/chat`

请求：

```json
{
  "query": "2026年转专业报名截止日期",
  "session_id": null,
  "top_k": 8,
  "time_filter": { "mode": "auto", "date_from": null, "date_to": null, "as_of": null }
}
```

`time_filter.mode` 枚举：`auto`（默认，由 B 做时间意图识别）、`off`（关闭时间过滤，**仅评估用**）、`range`（`date_from`/`date_to` 必填）。

`data`：

```json
{
  "answer": "2026年转专业报名截止日期为2026年4月10日……",
  "refused": false,
  "refuse_reason": null,
  "citations": [{
    "doc_id": "jwb-jxtz-2026-29459", "title": "关于2026年转专业工作的通知",
    "publish_date": "2026-03-04", "effective_from": "2026-03-04", "effective_to": "2026-05-31",
    "doc_version": "v2026.1", "section_path": "正文 > 一、报名安排",
    "source_url": "https://www.sgu.edu.cn/jwb/info/1050/29459.htm", "snippet": "……截止时间为4月10日17:00。",
    "score": 0.8213, "recency_bucket": "current"
  }],
  "retrieval": { "mode": "auto", "applied_filters": ["publish_date<=2026-11-08", "is_current=true"], "candidates": 42 },
  "latency_ms": 812,
  "answered_at": "2026-11-08T14:00:00+08:00"
}
```

`recency_bucket` 枚举：`current` | `stale` | `unknown`。`refused=true` 时 `answer` 为固定拒答话术、`citations` 可为空数组。

### 3.4 `GET /api/v1/chat/stream`（SSE）

Query：`q`（必填）、`top_k`、`token`（无 Authorization 头时使用）。
Content-Type `text/event-stream`。事件顺序固定：`meta` → `sources` → `delta`×N → `done`（出错则 `error` 后关闭）。

```
event: meta
data: {"session_id":"...","retrieval":{"mode":"auto","applied_filters":[],"candidates":42}}

event: sources
data: {"citations":[ <同 3.3 Citation 结构，去掉 snippet 可选> ]}

event: delta
data: {"text":"2026年转专业"}

event: done
data: {"latency_ms":912,"refused":false}
```

### 3.5 `GET /api/v1/eval/summary`

`data`：

```json
{
  "run_id": "run-20261108-01", "created_at": "2026-11-08T12:00:00+08:00",
  "dataset_size": 40,
  "metrics": {
    "faithfulness": 0.87, "answer_relevancy": 0.81,
    "context_precision": 0.74, "context_recall": 0.69,
    "stale_recall_rate": 0.08, "refusal_rate": 0.90
  },
  "by_year": [{ "year": 2026, "stale_recall_rate": 0.05, "count": 18 }],
  "regressions": [{ "metric": "context_precision", "delta": -0.06, "vs_run": "run-20261101-01" }]
}
```

### 3.6 内部接口（Python RAG 核心，仅 C 调用）

`POST /internal/retrieve` — 请求：

```json
{ "query": "2026年转专业报名截止日期", "time_filter": { "mode": "auto", "date_from": null, "date_to": null, "as_of": null }, "top_k": 20, "rerank": true }
```

响应（`chunks[]` 与 `scores[]` **等长同序**）：

```json
{
  "chunks": [{
    "chunk_id": "jwb-jxtz-2026-29459#c003", "doc_id": "jwb-jxtz-2026-29459",
    "title": "关于2026年转专业工作的通知", "text": "……截止时间为4月10日17:00。",
    "publish_date": "2026-03-04", "effective_from": "2026-03-04", "effective_to": "2026-05-31",
    "doc_version": "v2026.1", "section_path": "正文 > 一、报名安排",
    "content_type": "notice_body", "source_url": "https://www.sgu.edu.cn/jwb/info/1050/29459.htm",
    "is_current": true, "supersedes_doc_id": "jwb-jxtz-2025-29001"
  }],
  "scores": [0.8213],
  "applied_filters": ["publish_date<=2026-11-08", "is_current=true"],
  "retrieve_ms": 210
}
```

`GET /internal/health` — `{ "status": "ok", "components": { "qdrant": "ok", "embedder": "ok", "reranker": "ok" }, "version": "0.1.0" }`。

### 3.7 错误码

| code | HTTP | 含义 | 产生方 |
|---|---|---|---|
| 0 | 200 | ok | — |
| 40001 | 400 | 参数校验失败 | C |
| 40101 | 401 | JWT 缺失/过期/非法 | C |
| 40301 | 403 | dev 接口在生产被关闭 | C |
| 40401 | 404 | 资源不存在 | C |
| 42901 | 429 | 触发限流 | C |
| 50201 | 502 | RAG 核心不可用 | C |
| 50202 | 504 | RAG 核心超时（>8s） | C |
| 50301 | 503 | 依赖未就绪（向量库/DB） | B / C |
| 50001 | 500 | 未分类内部错误 | 任意 |

### 3.8 鉴权与限流

- JWT：HS256，claim `sub`、`role`(`student`|`admin`)、`exp`；密钥 `JWT_SECRET`。
- 限流（Redis 令牌桶，按 `sub`，按分钟）：notices 300/min，chat 30/min，chat/stream 20/min，eval/summary 60/min。
- 超限返回 42901，带 `Retry-After` 头。

---

## 4. 元数据 schema（CHUNK METADATA）

`ingestion/` 产出的 `chunks.jsonl` 每行一个 chunk，**顶层字段即检索文档字段**（扁平，不平铺 `metadata.` 前缀之外）。
字段名一旦被 `ingestion` 写出、被 `rag-core` 读取，即受本表约束。

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `chunk_id` | string | 必填 | `{doc_id}#c{序号3位}`，全库唯一，稳定（重新切块才变） |
| `doc_id` | string | 必填 | `jwb-jxtz-{year}-{序号4位}`，同一通知的多个版本 **doc_id 不同** |
| `title` | string | 必填 | 原文标题，不做摘要 |
| `publish_date` | string | 必填 | `YYYY-MM-DD`；解析失败填**列表页日期**，不填 null |
| `effective_period` | object | 必填 | `{ "from": "YYYY-MM-DD", "to": "YYYY-MM-DD"\|null, "precision": "day"\|"month"\|"term"\|"unknown" }`；**不得省略对象**，未知时 `{from:null,to:null,precision:"unknown"}` |
| `doc_version` | string | 必填 | `v{year}.{n}`，同年内按发布顺序递增；`doc_version_rank` 为可比较整数 `year*100+n` |
| `section_path` | string | 必填 | `正文 > 一、报名安排`；附件为 `附件 > {文件名}`；无结构填 `正文` |
| `content_type` | string | 必填 | 枚举 `notice_body`\|`attachment`\|`table`\|`appendix` |
| `source_url` | string | 必填 | 详情页绝对 URL，**必须**在 `ALLOWED_DOMAINS` 内 |
| `text` | string | 必填 | 切块正文，含标题行以便孤立召回；建议 320–800 tokens |
| `token_count` | int | 必填 | 用 bge-m3 tokenizer 估算 |
| `content_hash` | string | 必填 | `sha256(text)`，用于幂等 upsert |
| `supersedes_doc_id` | string\|null | 可选 | 仅在新版明确替代旧版时填写，**不得猜测** |
| `is_current` | bool | 必填 | 默认 `true`；同主题有更新版本且已交叉引用时旧版本置 `false` |
| `ingested_at` | string | 必填 | RFC3339 |

规则：
- **未知必须显式表达**（`null` / `unknown`），禁止用 `1970-01-01` 之类哨兵值。
- 新增字段一律可选，且 `rag-core` 必须容忍缺失（读取端写默认值）。
- 字段变更 = 契约变更，走 §1 流程，并触发 E 的全量重跑。
- `content_hash` 相同的 chunk 不重复写入（幂等）。

---

## 5. 时效性逻辑（TEMPORAL PIPELINE）

**顺序硬约束**（任何改动不得改变顺序）：

```
1. 时间意图识别 (B, rag-core/app/time_filter.py)
        query → { intent: "current"|"historical"|"deadline"|"none", as_of, date_from, date_to }
2. 硬过滤（在向量检索之前，作为 Qdrant payload filter）
        publish_date <= as_of  AND  (effective_period.to IS NULL OR effective_period.to >= as_of)
3. 语义召回（dense + sparse，各取 top 40）
4. RRF 融合（Qdrant 原生 RRF，k=2）
5. Rerank（bge-reranker，top 20 → top 8）
6. 过期护栏：intent=current 时，对 is_current=false 的 chunk 做降权/剔除，并在 applied_filters 记录
7. 拒答/答案生成（无 chunk 得分 ≥ 阈值或全部过期 → refused=true）
```

**改动规则**：

- 任何触及步骤 1/2/6 的改动（含 Qdrant filter 字段、阈值、降权系数），PR 必须：
  1. 在描述中注明「影响时效性链路：是/否」；若为"是"，写明影响哪一步；
  2. 由 **E** 跑 `eval/` 全量回归并在 PR 贴出 `stale_recall_rate` 与 `faithfulness` 前后对比；
  3. 未通过回归不得合并。
- 禁止为了提升召回率而**放宽步骤 2 的硬过滤**；如需放宽，只能在 `mode="off"`（评估用）下进行。
- `mode="off"` 只允许出现在 E 的评估代码与单元测试中，**禁止**出现在默认路径。

**评估口径（与 E 共用，不得各自发明）**：

- 旧版本误召回率 `stale_recall_rate` = 命中"含 superseded chunk 的 top-3 引用"的陷阱题数 ÷ 陷阱题总数（分母 ≥10，不足则本轮评估作废）。
- 拒答率 `refusal_rate` = `refused=true` 的无答案题数 ÷ 无答案题总数（分母 ≥10）。
- 阈值以 `eval/thresholds.json` 为准，改阈值同样需要 E 确认。

---

## 6. 禁止事项（PROHIBITIONS）

违反任意一条，PR 直接打回：

1. **不新增 compose 服务、不改服务名与端口**（清单见 `docs/deployment.md §3` 与 `docs/architecture.md §5`，A—F 一致）。需要新依赖时先和 F 谈，由 F 改 `docker-compose.yml`。`qdrant-init` 是 F 维护的一次性服务，**不属于可改范围**；`rag-core` 只走 `expose`，**不得**改回 `ports`（那会让 `/internal/*` 绕过 nginx）。
2. **不修改 API 路径、方法、字段名或类型**；不把可选字段变必填；不在响应里塞未登记字段。
3. **前端不得硬编码后端地址**：只准读 `import.meta.env.VITE_API_BASE_URL`，默认 `/api`，由 Nginx 反代。
4. **不绕过受限采集参数**：所有抓取必须经过 `ALLOWED_DOMAINS` / `REQUEST_DELAY_SECONDS`(≥1) / `MAX_PAGES_PER_RUN`(列表页) / `MAX_REQUESTS_PER_RUN`(含详情页与重试) **四道闸门**；**禁止**递归抓全站、禁止并发无节流、禁止登录态抓取、禁止抓取 robots 禁止路径、**禁止为绕过 WAF 403 而提高并发或缩短延时**（那是把课程项目变成攻击）。
5. **不假设官网有 API 或 RSS**：采集层只按 HTML 列表页 + 详情页解析实现；不得引入"接口抓取"分支。
6. **不让任何一人的工作量超过总量 25%**：跨链路重构先在群里拆任务，不要一个人改三个目录。
7. **不提交密钥与数据集原文**：正文落库，`chunks.jsonl` 不入 Git；`.env` 不入 Git，只提交 `.env.example`。
8. **不在 `main` 上直接推送**；每人一个长分支 `feat/{a|b|c|d|e|f}-*`，至少 1 人 review。
9. **不把时间过滤"顺手优化"掉**：见 §5，任何绕过 §5 顺序的实现都算回归。
10. **不新建文档绕过本 Skill**：契约类信息只有一个来源——本文件 + `references/api-contract.md`。

---

## 附录：快速自检（提交前）

```bash
bash .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh
```

输出了 `✗` 行就必须修；`!` 行是提醒（例如改了 `docs/api.md` 但没改 references）。
