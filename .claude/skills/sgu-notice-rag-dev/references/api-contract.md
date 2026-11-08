# api-contract.md — 冻结契约摘要（只读引用）

> 本文件是 `docs/api.md` 的**摘要**，不是副本。两者路径/字段不一致时：**以本文件为准**，并按 `SKILL.md §1` 三步流程同步。
> 完整请求/响应示例、错误码表、SSE 时序见 `docs/api.md`。

## 0. 通用约定

- Base path：`/api/v1`（对外，Go API）；`/internal`（内部，Python RAG 核心，仅 C 调用）。
- 统一响应包：`{"code":0,"message":"ok","data":{}}`；`code != 0` 时 `data` 可为 `null`。
- Content-Type：`application/json; charset=utf-8`；时间为 **Asia/Shanghai**，日期 `YYYY-MM-DD`，时刻 RFC3339。
- 鉴权：`Authorization: Bearer <jwt>`，HS256。
- 分页：`page` 从 1 开始，`page_size` 默认 20、上限 100。
- 存活探针 `GET /healthz`（不在 `/api` 下，不鉴权不限流）：进程活着即 200，依赖状态只写在
  `data.components` 里。**依赖 down 不改状态码** —— 否则 compose 会进重启循环。

## 1. 端点总表

| 路径 | 方法 | 鉴权 | 成员 | 说明 |
|---|---|---|---|---|
| `/api/v1/auth/token` | POST | 公开（仅 dev） | C | 签发 JWT，生产置 403 |
| `/api/v1/notices` | GET | 公开 | C | 通知分页 + 时间范围过滤 |
| `/api/v1/chat` | POST | JWT | C | 非流式问答 |
| `/api/v1/chat/stream` | GET | JWT | C | SSE 流式问答 |
| `/api/v1/eval/summary` | GET | JWT | C | 评估看板数据 |
| `/healthz` | GET | 公开 | C | 存活探针 |
| `/internal/retrieve` | POST | 内网 | B | 检索 + 时间过滤 + 重排 |
| `/internal/health` | GET | 内网 | B | 依赖健康 |

## 2. `GET /api/v1/notices`

**Query**

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` | int | 1 | 页码 |
| `page_size` | int | 20 | ≤100 |
| `year` | int | — | 按 `publish_date` 的年份 |
| `q` | string | — | 标题模糊匹配（不含正文） |
| `publish_from` | date | — | `publish_date >=` |
| `publish_to` | date | — | `publish_date <=` |
| `content_type` | string | — | 枚举同 chunk schema |
| `doc_version` | string | — | 精确匹配 |

**data**

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
  "total": 2,
  "page": 1,
  "page_size": 20
}
```

- `items` 字段集**固定**；`effective_from`/`effective_to` 为 `effective_period` 的**平铺**表示，未知为 `null`。
- `is_current` 由 ingestion 判定，API 只读不改。

## 3. `POST /api/v1/chat`

**请求**

```json
{
  "query": "2026年转专业报名截止日期",
  "session_id": null,
  "top_k": 8,
  "time_filter": { "mode": "auto", "date_from": null, "date_to": null, "as_of": null }
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | — | 1–512 字符 |
| `session_id` | string\|null | 否 | null | 预留给多轮，**v1 不实现记忆** |
| `top_k` | int | 否 | 8 | 1–20 |
| `time_filter.mode` | string | 否 | `auto` | `auto`\|`off`\|`range` |
| `time_filter.date_from` | date\|null | `range` 时必填 | null | |
| `time_filter.date_to` | date\|null | `range` 时必填 | null | |
| `time_filter.as_of` | date\|null | 否 | null（服务端取当天） | 以哪一天为"现在"判定时效 |

**响应 data**

```json
{
  "answer": "2026年转专业报名截止日期为2026年4月10日17:00。",
  "refused": false,
  "refuse_reason": null,
  "citations": [{
    "doc_id": "jwb-jxtz-2026-29459",
    "title": "关于2026年转专业工作的通知",
    "publish_date": "2026-03-04",
    "effective_from": "2026-03-04",
    "effective_to": "2026-05-31",
    "doc_version": "v2026.1",
    "section_path": "正文 > 一、报名安排",
    "source_url": "https://www.sgu.edu.cn/jwb/info/1050/29459.htm",
    "snippet": "……报名截止时间为4月10日17:00。",
    "score": 0.8213,
    "recency_bucket": "current"
  }],
  "retrieval": { "mode": "auto", "applied_filters": ["effective_from<=2026-11-08", "effective_to>=2026-11-08 OR effective_to IS NULL"], "candidates": 42 },
  "latency_ms": 812,
  "answered_at": "2026-11-08T14:00:00+08:00"
}
```

- `refused=true` → `answer` 为固定话术（"未在教学通知中找到可回答该问题的现行依据……"），`citations` 数组可为空。
- `recency_bucket` 枚举 `current`|`stale`|`unknown`。

## 4. `GET /api/v1/chat/stream`（SSE）

Query：`q`（必填）、`top_k`、`token`（无 Authorization 头时的兜底）。

事件顺序固定：`meta` → `sources` → `delta`×N → `done`；异常时为 `error` 后关闭。

```
event: meta
data: {"session_id":"s-1","retrieval":{"mode":"auto","applied_filters":[],"candidates":42}}

event: sources
data: {"citations":[{ ... Citation，可省 snippet ... }]}

event: delta
data: {"text":"2026年转专业"}

event: done
data: {"latency_ms":912,"refused":false}
```

- 必须发送 `: keepalive` 注释行，间隔 ≤15s，防 Nginx 60s 超时。
- `error` 事件形如 `{"code":50201,"message":"rag core unavailable"}`。

## 5. `GET /api/v1/eval/summary`

```json
{
  "run_id": "run-20261108-01",
  "created_at": "2026-11-08T12:00:00+08:00",
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

- 指标名即 RAGAS 指标名的小写形式，**不得改名**；自定义指标仅 `stale_recall_rate`、`refusal_rate` 两个。
- 无数据时 `run_id=null`、`metrics` 全为 `null`、`dataset_size=0`。

## 6. `POST /internal/retrieve`（内部）

**请求**

```json
{
  "query": "2026年转专业报名截止日期",
  "time_filter": { "mode": "auto", "date_from": null, "date_to": null, "as_of": null },
  "top_k": 20,
  "rerank": true
}
```

**响应**：`chunks[]`（Chunk 结构，见 `data-model.md`）与 `scores[]` **等长同序**。

```json
{
  "chunks": [{
    "chunk_id": "jwb-jxtz-2026-29459#c003",
    "doc_id": "jwb-jxtz-2026-29459",
    "title": "关于2026年转专业工作的通知",
    "text": "……",
    "publish_date": "2026-03-04",
    "effective_from": "2026-03-04",
    "effective_to": "2026-05-31",
    "doc_version": "v2026.1",
    "section_path": "正文 > 一、报名安排",
    "content_type": "notice_body",
    "source_url": "https://www.sgu.edu.cn/jwb/info/1050/29459.htm",
    "is_current": true,
    "supersedes_doc_id": null
  }],
  "scores": [0.8213],
  "applied_filters": ["effective_from<=2026-11-08", "effective_to>=2026-11-08 OR effective_to IS NULL"],
  "retrieve_ms": 210
}
```

`applied_filters` 是**给人看的伪 SQL**，固定两种形态（`rag-core/app/retriever.py:_build_filter()` 与 filter 同分支产出，不许事后拼）：

- `mode=auto`（无 `as_of`）→ `["effective_from<={今天}", "effective_to>={今天} OR effective_to IS NULL"]`
- `mode=range` → `["effective_from<={date_to}", "effective_to>={date_from} OR effective_to IS NULL"]`
- `mode=off` → `[]`（E 靠空数组区分评估口径）

`OR effective_to IS NULL` 不可省：长期有效的规定没有截止日，只写 `effective_to>=X` 会把《学籍管理规定》整条滤掉。

`GET /internal/health` → `{"status":"ok","components":{"qdrant":"ok","embedder":"ok","reranker":"ok"},"version":"0.1.0"}`。

## 6.5 字段速查（前端/后端对字段名的唯一依据）

**通知与引用共用的标识字段**（`NoticeItem` / `Citation` / `Chunk` 都有）：
`doc_id`、`title`、`publish_date`、`effective_from`、`effective_to`、`doc_version`、
`section_path`、`content_type`、`source_url`、`is_current`、`supersedes_doc_id`。

**Citation 额外字段**：`score`、`recency_bucket`（非流式还有 `snippet`）。

**Chunk 额外字段**：`chunk_id`（形如 `{doc_id}#c003`）、`text`。

**不可混用的两组名字**（踩过）：

| 场景 | 正确 | 错误 |
|---|---|---|
| 有效期 | `effective_from` / `effective_to` | `effective_period`（那是 chunks.jsonl 的嵌套形态，**不进 HTTP 响应**） |
| 来源链接 | `source_url` | `url` |
| 时效判定 | `recency_bucket`（`current`\|`stale`\|`unknown`） | `is_expired`、`expired` |

字段新增只能"加可选字段"，且必须同时改 `docs/api.md`、本文件、Go 的 `model`/`client` 结构体、
前端 `src/api/types.ts` 与 `mock/fixtures.ts`，否则 `frontend/src/api/__tests__/contract.spec.ts` 会红。

## 7. 错误码

| code | HTTP | 含义 |
|---|---|---|
| 0 | 200 | ok |
| 40001 | 400 | 参数校验失败 |
| 40101 | 401 | JWT 缺失/过期/非法 |
| 40301 | 403 | dev 接口在生产关闭 |
| 40401 | 404 | 不存在 |
| 42901 | 429 | 限流 |
| 50201 | 502 | RAG 核心不可用 |
| 50202 | 504 | RAG 核心超时（>8s） |
| 50301 | 503 | 依赖未就绪 |
| 50001 | 500 | 内部错误 |

## 8. 限流配额（按 `sub`，每分钟）

| 路径 | QPS 上限（次/分） |
|---|---|
| `/api/v1/notices` | 300 |
| `/api/v1/chat` | 30 |
| `/api/v1/chat/stream` | 20 |
| `/api/v1/eval/summary` | 60 |

超限 42901 + `Retry-After`。

## 9. 变更记录

| 日期 | 变更 | 提出 | 确认 |
|---|---|---|---|
| 2026-11-08 | v1.0.0-contract-freeze 首次冻结 | C / D | A B E F |
