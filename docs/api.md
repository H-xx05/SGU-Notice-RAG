# API 契约（冻结）

> **状态：FROZEN — `v1.0.0-contract-freeze`（2026-11-08）**
> 冻结后只允许"新增可选字段"；改字段名/类型/路径/语义必须走 `SKILL.md §1` 三步流程。
> 本文件的摘要版在 `.claude/skills/sgu-notice-rag-dev/references/api-contract.md`，**两者路径与字段必须逐字一致**（CI 用 `check-contract.sh` 校验）。

- 对外服务：Go + Gin，`api-server/`，Base `http://localhost:8080`
- 内部服务：Python RAG Core，`rag-core/`，Base `http://rag-core:8000`（仅内网可达）
- 生产入口：Nginx `http://localhost:8088`，`/api/*` 反代到 Go，`/` 反代到前端静态资源

---

## 0. 通用约定

### 0.1 统一响应包

```json
{ "code": 0, "message": "ok", "data": { } }
```

- `code`：业务码，`0` 表示成功，非 0 见 §7。
- 失败时 `data` 为 `null`，`message` 为**面向用户的中文**短句。
- SSE 端点（§4）**不使用**此包装，直接发送 event。

### 0.2 时间与编码

- 时区统一 `Asia/Shanghai`；日期 `YYYY-MM-DD`；时刻 RFC3339（`2026-11-08T14:00:00+08:00`）。
- 所有 JSON 字段名 `snake_case`；UTF-8。

### 0.3 鉴权

- `Authorization: Bearer <jwt>`，HS256，claims：`sub`（用户标识）、`role`（`student`|`admin`）、`exp`、`iat`。
- 密钥 `JWT_SECRET`（`.env`），默认 TTL 12h。
- SSE 若无 Authorization 头，允许 `?token=<jwt>` 兜底（`EventSource` 不能设头）。

### 0.4 分页

`page` 从 1 开始；`page_size` 默认 20，上限 100；越界返回 `40001`。

### 0.5 存活探针

`GET /healthz`（**不挂在 `/api` 下**，compose 与 Nginx 直接探它）

```json
{ "code": 0, "message": "ok", "data": {
  "status": "ok",
  "components": { "postgres": "ok", "rag_core": "ok", "redis": "ok" },
  "version": "0.1.0"
} }
```

- **依赖 down 不改 HTTP 状态码**：进程活着就返回 200，依赖状态只在 `data.components` 里体现。
  探针判死会让 compose 进入重启循环，一次 Redis 抖动就能把整个网关打挂 —— 探测的目的是
  "告诉运维谁坏了"，不是"让编排器杀了我"。
- `status` 为 `degraded` 的判定：任一组件不等于 `ok`。
- 该端点**不鉴权、不限流**，也不出现在 `/api/v1` 前缀下。

---

## 1. `POST /api/v1/auth/token`

**仅开发/演示用**。生产（`APP_ENV=production`）返回 `40301`。

请求：

```json
{ "user_id": "stu-20260001", "role": "student" }
```

响应：

```json
{
  "code": 0, "message": "ok",
  "data": { "token": "eyJhbGciOiJIUzI1NiIs...", "expires_at": "2026-11-09T02:00:00+08:00" }
}
```

---

## 2. `GET /api/v1/notices`

分页查询通知列表，支持时间范围过滤。

**Query**

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `page` | int | 否 | 1 | 页码 |
| `page_size` | int | 否 | 20 | ≤100 |
| `year` | int | 否 | — | `publish_date` 的年份 |
| `q` | string | 否 | — | 标题模糊匹配（不含正文） |
| `publish_from` | date | 否 | — | `publish_date >= publish_from` |
| `publish_to` | date | 否 | — | `publish_date <= publish_to` |
| `content_type` | string | 否 | — | `notice_body`\|`attachment`\|`table`\|`appendix` |
| `doc_version` | string | 否 | — | 精确匹配 |
| `include_stale` | bool | 否 | `true` | `false` 时只返回 `is_current=true` |

**响应**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "items": [
      {
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
      }
    ],
    "total": 2,
    "page": 1,
    "page_size": 20
  }
}
```

**错误**：`40001`（参数非法）、`50301`（Postgres 不可用）、`50001`。

---

## 3. `POST /api/v1/chat`

非流式问答。C 收到请求后调用 B 的 `/internal/retrieve`，再用模板/LLM 组织答案并返回。

**请求**

```json
{
  "query": "2026年转专业报名截止日期",
  "session_id": null,
  "top_k": 8,
  "time_filter": { "mode": "auto", "date_from": null, "date_to": null, "as_of": null }
}
```

| 字段 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `query` | string | 是 | — | 1–512 字符，去首尾空白后非空 |
| `session_id` | string\|null | 否 | null | 预留；v1 不实现记忆 |
| `top_k` | int | 否 | 8 | 1–20 |
| `time_filter` | object | 否 | `{"mode":"auto"}` | 见下 |
| `time_filter.mode` | string | 否 | `auto` | `auto`\|`off`\|`range`；`off` 仅评估用 |
| `time_filter.date_from` | date\|null | `range` 时必填 | null | |
| `time_filter.date_to` | date\|null | `range` 时必填 | null | |
| `time_filter.as_of` | date\|null | 否 | null→服务端今天 | 时效判定基准日 |

**响应**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "answer": "2026年转专业报名截止日期为2026年4月10日17:00，逾期不再受理。",
    "refused": false,
    "refuse_reason": null,
    "citations": [
      {
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
      }
    ],
    "retrieval": {
      "mode": "auto",
      "applied_filters": ["effective_from<=2026-11-08", "effective_to>=2026-11-08 OR effective_to IS NULL"],
      "candidates": 42
    },
    "latency_ms": 812,
    "answered_at": "2026-11-08T14:00:00+08:00"
  }
}
```

**拒答语义**：`refused=true` 时 `answer` 固定为

> 未在教学通知中找到可回答该问题的现行依据。可能原因：该事项本年度未发布通知，或相关通知已过期。建议直接查阅教务处网站。

`refuse_reason` 枚举：`no_hit`（无召回）、`all_stale`（召回全为过期版本）、`low_score`（最高分低于阈值）。

**错误**：`40001`、`40101`、`42901`、`50201`（RAG 核心不可用）、`50202`（超时 >8s）。

---

## 4. `GET /api/v1/chat/stream`（SSE）

**Query**

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `q` | string | 是 | — |
| `top_k` | int | 否 | 8 |
| `token` | string | 否 | — |

**响应头**：`Content-Type: text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`（Nginx 不缓冲）。

**事件序列（顺序固定）**

```
event: meta
data: {"session_id":"s-7f2c","retrieval":{"mode":"auto","applied_filters":[],"candidates":42}}

event: sources
data: {"citations":[{"doc_id":"jwb-jxtz-2026-29459","title":"关于2026年转专业工作的通知","publish_date":"2026-03-04","effective_from":"2026-03-04","effective_to":"2026-05-31","doc_version":"v2026.1","section_path":"正文 > 一、报名安排","source_url":"https://www.sgu.edu.cn/jwb/info/1050/29459.htm","score":0.8213,"recency_bucket":"current"}]}

event: delta
data: {"text":"2026年转专业"}

event: delta
data: {"text":"报名截止日期为2026年4月10日17:00。"}

event: done
data: {"latency_ms":912,"refused":false}
```

- `delta` 至少 1 个，可为 N 个（按 token 或按句切片，由 B 决定，C 原样转发）。
- 保活：每 ≤15s 发一行 `: keepalive` 注释，防 Nginx `proxy_read_timeout 60s`。
- 异常：发 `event: error` + `data: {"code":50201,"message":"rag core unavailable"}` 后关闭。
- 客户端断开：C 必须 2s 内取消上游 HTTP 请求（context 传递）。

---

## 5. `GET /api/v1/eval/summary`

评估看板数据来源；E 跑批写入 Postgres，C 读出。

**Query**：`run_id`（可选，缺省取最近一次）。

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "run_id": "run-20261108-01",
    "created_at": "2026-11-08T12:00:00+08:00",
    "dataset_size": 40,
    "metrics": {
      "faithfulness": 0.87,
      "answer_relevancy": 0.81,
      "context_precision": 0.74,
      "context_recall": 0.69,
      "stale_recall_rate": 0.08,
      "refusal_rate": 0.90
    },
    "by_year": [
      { "year": 2026, "stale_recall_rate": 0.05, "count": 18 },
      { "year": 2025, "stale_recall_rate": 0.11, "count": 9 }
    ],
    "regressions": [
      { "metric": "context_precision", "delta": -0.06, "vs_run": "run-20261101-01" }
    ]
  }
}
```

无数据时：`run_id: null`、`dataset_size: 0`、`metrics` 六项全 `null`、`by_year: []`、`regressions: []`。

**指标名冻结**：`faithfulness`、`answer_relevancy`、`context_precision`、`context_recall`、`stale_recall_rate`、`refusal_rate`。

---

## 6. 内部接口（Python RAG Core，仅内网）

### 6.1 `POST /internal/retrieve`

**请求**

```json
{
  "query": "2026年转专业报名截止日期",
  "time_filter": { "mode": "auto", "date_from": null, "date_to": null, "as_of": null },
  "top_k": 20,
  "rerank": true
}
```

**响应**（`chunks[]` 与 `scores[]` **等长同序**，长度 ≤ `top_k`）

```json
{
  "chunks": [
    {
      "chunk_id": "jwb-jxtz-2026-29459#c003",
      "doc_id": "jwb-jxtz-2026-29459",
      "title": "关于2026年转专业工作的通知",
      "text": "……报名截止时间为4月10日17:00。",
      "publish_date": "2026-03-04",
      "effective_from": "2026-03-04",
      "effective_to": "2026-05-31",
      "doc_version": "v2026.1",
      "section_path": "正文 > 一、报名安排",
      "content_type": "notice_body",
      "source_url": "https://www.sgu.edu.cn/jwb/info/1050/29459.htm",
      "is_current": true,
      "supersedes_doc_id": null
    }
  ],
  "scores": [0.8213],
  "applied_filters": ["effective_from<=2026-11-08", "effective_to>=2026-11-08 OR effective_to IS NULL"],
  "retrieve_ms": 210
}
```

- `applied_filters` 必须**如实**反映实际生效的过滤条件；`mode=off` 时为空数组（评估口径依赖此字段区分）。
  字符串是**给人看的伪 SQL**，固定两种形态：
  - `mode=auto`（无 `as_of`）：`["effective_from<={今天}", "effective_to>={今天} OR effective_to IS NULL"]`
  - `mode=range`（有 `date_from`/`date_to`）：`["effective_from<={date_to}", "effective_to>={date_from} OR effective_to IS NULL"]`
  `OR effective_to IS NULL` 不是可选项：长期有效的规定（如《学籍管理规定》）没有截止日，
  只写 `effective_to>=X` 会把它们整条过滤掉。字符串由 `rag-core/app/retriever.py:_build_filter()`
  与 filter 在同一分支产出，**不允许事后拼**（否则文案与真实过滤会各说各话）。
- `rerank=false` 时 `scores` 为 RRF 融合分（无 rerank 分），C 不区分。

### 6.2 `GET /internal/health`

```json
{
  "status": "ok",
  "components": { "qdrant": "ok", "embedder": "ok", "reranker": "ok" },
  "version": "0.1.0"
}
```

`status` ∈ `ok`|`degraded`；组件值 ∈ `ok`|`down`|`loading`。任一 `down` → HTTP 503 + `status=degraded`。

---

## 7. 错误码表

| code | HTTP | 含义 | 由谁返回 | 前端处理 |
|---|---|---|---|---|
| 0 | 200 | ok | — | — |
| 40001 | 400 | 参数校验失败 | C | 提示 `message` |
| 40101 | 401 | JWT 缺失/过期/非法 | C | 跳转取 token 后重试 |
| 40301 | 403 | dev 接口在生产关闭 | C | 提示不可用 |
| 40401 | 404 | 资源不存在 | C | 空态页 |
| 42901 | 429 | 触发限流 | C | 按 `Retry-After` 退避 |
| 50201 | 502 | RAG 核心不可用 | C | 提示稍后重试 |
| 50202 | 504 | RAG 核心超时（>8s） | C | 同上 |
| 50301 | 503 | 依赖未就绪 | B/C | 展示降级态 |
| 50001 | 500 | 未分类内部错误 | 任意 | 通用错误页 |

---

## 8. 限流（Redis 令牌桶，键 `rl:{sub}:{route}`）

| 路径 | 每分钟 | 突发 |
|---|---|---|
| `/api/v1/notices` | 300 | 60 |
| `/api/v1/chat` | 30 | 10 |
| `/api/v1/chat/stream` | 20 | 5 |
| `/api/v1/eval/summary` | 60 | 10 |
| `/api/v1/auth/token` | 10 | 5 |

超限返回 `42901` + `Retry-After: <秒>`。

---

## 9. 契约的落地方式（谁在什么地方实现它）

1. 本文件是**唯一人工编辑入口**；改完必须同步 `.claude/skills/sgu-notice-rag-dev/references/api-contract.md`（见 `SKILL.md §1`）。
2. **C 的实现**在 `api-server/internal/model/types.go`（结构体 + json tag）。骨架期**不生成 `swagger.yaml`**：手写 OpenAPI 与 Go 结构体长期会漂移，而 `router_test.go` 已把六条路由与本文档逐字比对，`check-contract.sh` 再做一次路径集合检查 —— 两份手写物比三份更难骗过 CI。【预留】C 若引入 Swagger，须让注释生成的 spec 与本文档 diff 为空再接 CI，不要手抄。
3. **D 的实现**在 `frontend/src/api/types.ts` + `client.ts`。Mock 是 **TypeScript 常量**（`src/api/mock/fixtures.ts`），不是 JSON 文件 —— 这样 `tsc` 能在编译期就发现字段缺失，JSON 要等到运行时。
   `src/api/__tests__/contract.spec.ts` 用**键集合逐字比对**锁死形状（`CHAT_KEYS` 含 `refuse_reason`、`CITATION_KEYS` 含 `recency_bucket` 等），字段增删会直接让测试红。
4. **B 的实现**在 `rag-core/app/schemas.py`（`extra="forbid"`），`rag-core/tests/test_api.py` 断言 `/internal/retrieve` 响应顶层恰好 4 键、chunk 恰好 13 键。
5. **A 的实现**在 `ingestion/ingestion/emit/chunk_writer.py` 的 `CONTRACT_FIELDS`（17 个 chunk 字段），`ingestion.cli check <path>` 可独立校验产物。

---

## 10. 变更记录

| 日期 | 版本 | 变更 | 提出 | 确认 |
|---|---|---|---|---|
| 2026-11-08 | v1.0.0 | 首次冻结：6 个对外端点 + 2 个内部端点 | C / D | A B E F |
