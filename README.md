# SGU-Notice-RAG

> 韶关学院教务部教学通知的**时效性**检索问答系统 —— 不只是"找到相关通知"，而是**确认哪一版现在还算数**。

数据源：[`www.sgu.edu.cn/jwb/jxtz.htm`](https://www.sgu.edu.cn/jwb/jxtz.htm)（公开 HTML 列表页，无 API / 无 RSS）。
核心挑战：同一事项多年多版本共存，语义检索会把已失效的旧版本当成现行规则召回 —— 我们称之为**时效性幻觉**。

---

## 一键启动

```bash
cp .env.example .env
docker compose up -d --build
```

首次约 2–5 分钟。起来后：

| 入口 | 地址 |
|---|---|
| 前端页面 | <http://localhost:8088> |
| API 健康检查 | <http://localhost:8088/healthz> |
| 只跑前端（Mock，零后端） | `cd frontend && pnpm i && VITE_USE_MOCK=true pnpm dev` → <http://localhost:5173> |

> 全部 7 个常驻容器 + 1 个一次性 `qdrant-init` 的端口、健康检查、排障见 [`docs/deployment.md`](docs/deployment.md)。

---

## 最小可跑示例

**① 取 token（仅开发环境）**

```bash
TOKEN=$(curl -s -X POST http://localhost:8088/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"stu-20260001","role":"student"}' | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
echo "$TOKEN"
```

**② 问一个有时效陷阱的问题**

```bash
curl -s -X POST http://localhost:8088/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"2026年转专业报名截止日期","time_filter":{"mode":"auto"}}'
```

返回（骨架阶段 `answer` 由 **C 侧的抽取式生成**产出，不调外部 LLM；`citations` 结构已冻结）：

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
    "retrieval": { "mode": "auto", "applied_filters": ["effective_from<=2026-11-08", "effective_to>=2026-11-08 OR effective_to IS NULL"], "candidates": 42 },
    "latency_ms": 812,
    "answered_at": "2026-11-08T14:00:00+08:00"
  }
}
```

**③ 流式问答（SSE）**

```bash
curl -N "http://localhost:8088/api/v1/chat/stream?q=2026年转专业报名截止日期&token=$TOKEN"
```

依次收到 `meta` → `sources` → `delta`… → `done`。

**④ 浏览器操作路径**：打开 <http://localhost:8088> → 顶部搜索框输入"转专业报名截止日期" → 回车 → 左侧通知列表按 `publish_date` 倒序、过期版本置灰标 `旧` → 右侧问答区逐字输出答案，每段答案下方列出引用卡片，点击跳转原文。

---

## 配置表

| 变量 | 默认值 | 作用 | 归属 |
|---|---|---|---|
| `RAG_CORE_URL` | `http://rag-core:8000` | Go API 调用 Python RAG 核心的地址（唯一内部下游） | C |
| `VECTOR_DB_URL` | `http://qdrant:6333` | Qdrant 地址 | B / A |
| `SCRAPER_MAX_PAGES` | `50` | 单次采集最多翻多少页**列表页**（详情页不计入） | A |
| `SCRAPER_DELAY_SECONDS` | `1.0` | 每次请求间隔，**下限 1 秒**，配更低会被压回 1.0（不是报错，是不给你更快） | A |
| `VITE_USE_MOCK` | `true` | 前端是否走本地 Mock（`true` 时零后端可跑） | D |

上表是元提示词冻结的五个变量名。采集侧另有正式长名（`SCRAPER_MAX_PAGES_PER_RUN`、
`SCRAPER_REQUEST_DELAY_SECONDS`）与第四道闸门 `SCRAPER_MAX_REQUESTS_PER_RUN`（含详情页与重试，硬顶 2000），
`ingestion/ingestion/config.py` **两种拼法都读**，以正式长名为准 —— 变量名写错等于闸门失效，所以不做静默忽略。

完整变量表（含 JWT、DSN、模型名共 **30 项**）见 [`docs/deployment.md §4`](docs/deployment.md)。

---

## 文档导航

| 文档 | 读者 | 内容 |
|---|---|---|
| [`docs/kickoff.md`](docs/kickoff.md) | **新人第一天先看这个** | 六人各自的开工步骤、本周五交什么、契约变更流程 |
| [`docs/kickoff-announcement.md`](docs/kickoff-announcement.md) | 群通知文案 | 项目启动时发群里的那条消息（含六个 Issue 与仓库链接） |
| [`docs/prd.md`](docs/prd.md) | 全员 / 评审 | 问题陈述、persona、成功指标、范围、验收 |
| [`docs/api.md`](docs/api.md) | C / D | **冻结契约**：6 个对外端点 + 2 个内部端点 |
| [`docs/data-model.md`](docs/data-model.md) | A / B / E | chunk schema、DDL、时间过滤顺序 |
| [`docs/architecture.md`](docs/architecture.md) | 全员 | Mermaid 架构图、时序图、目录↔分工映射 |
| [`docs/deployment.md`](docs/deployment.md) | F / 全员 | 从零到可访问、端口、排障、CI |
| [`docs/adr/001-gin-over-fastapi.md`](docs/adr/001-gin-over-fastapi.md) | C / F | 为什么 Go 而非 FastAPI |
| [`docs/adr/002-bge-m3-for-chinese.md`](docs/adr/002-bge-m3-for-chinese.md) | B / E | 为什么 bge-m3 + RRF + rerank |

---

## 开发前必读

开发前请加载 [`.claude/skills/sgu-notice-rag-dev/SKILL.md`](.claude/skills/sgu-notice-rag-dev/SKILL.md) —— **API 契约、目录归属、时效性逻辑与禁止事项以该 Skill 为准**，代码与它冲突时以它为对。

提交前跑一次契约自检：

```bash
bash .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh
```

---

## 六人分工

| 成员 | 链路 | 目录 | 第 4 周产出 |
|---|---|---|---|
| **A** | 采集与 ETL | `ingestion/` | 定时采集脚本 + `chunks.jsonl` |
| **B** | 检索与 RAG 核心 | `rag-core/` | `POST /internal/retrieve` 可用 |
| **C** | Go 高并发 API | `api-server/` | 全路由注册 + SSE 连通 |
| **D** | Vue 3 前端 | `frontend/` | 虚拟滚动 + SSE 问答 + 评估看板（Mock 可跑） |
| **E** | 评估体系 | `eval/` | 40 条 query + 基线指标表 |
| **F** | 基础设施 | `infra/` | `docker compose up` 一键启动 + CI 三绿 |

---

## 目录结构

```
SGU-Notice-RAG/
├── README.md                  本文件
├── docker-compose.yml         7 服务编排（nginx/frontend/api-server/rag-core/qdrant/postgres/redis）
├── .env.example              30 项环境变量（含采集四道闸门）
├── .gitattributes             行尾统一为 LF（Windows 上 clone 不会被 CRLF 污染）
├── .github/workflows/ci.yml   三 job：lint / test / build
│
├── .claude/skills/sgu-notice-rag-dev/          【契约唯一入口】
│   ├── SKILL.md                               六区块：契约优先级 / 目录归属 / 冻结清单 / schema / 时效 / 禁止事项
│   ├── references/                            api-contract.md · data-model.md · tech-baseline.md
│   └── scripts/check-contract.sh              PR 前必跑，CI 第一道门
│
├── docs/
│   ├── kickoff.md             六人启动指令（新人第一天先看这个）
│   ├── kickoff-announcement.md  项目启动群通知文案
│   ├── prd.md                 PRD：问题 / persona / 5 项指标 / 范围
│   ├── api.md                 冻结契约：6 对外 + 2 内部端点
│   ├── data-model.md          chunk schema / DDL / 时间过滤顺序
│   ├── architecture.md        Mermaid 架构图 + 两张时序图 + 分工映射
│   ├── deployment.md          从零到可访问 / 端口 / 排障 / CI
│   └── adr/                   001-gin-over-fastapi · 002-bge-m3-for-chinese
│
├── ingestion/                 【A】采集 ETL（零第三方依赖，纯标准库）
│   ├── ingestion/             config · fetcher · parsers/ · temporal · doc_registry
│   │                          · chunking/splitter · emit/chunk_writer · pipeline · cli
│   ├── tests/                 72 例（解析器用真实站点片段做回归）
│   └── data/                  chunks.jsonl 落地目录（gitignore）
│
├── rag-core/                  【B】检索核心（FastAPI）
│   ├── app/                   main · retriever · time_filter · store · embedder
│   │                          · schemas · settings · timeline · tools/pull_models
│   └── tests/                 43 例（含契约漂移探测、golden 排序）
│
├── api-server/                【C】Go + Gin
│   ├── cmd/server/main.go     启动序 / 优雅退出
│   ├── internal/              handler · middleware · client · store · model · response · router
│   └── ...                    全路由已注册，JWT + 限流链完整
│
├── frontend/                  【D】Vue 3 + TS + Vite + Element Plus + ECharts
│   └── src/                   api/（按 api.md 手写 + Mock）· views/ · components/ · router/
│
├── eval/                      【E】评估（指标零依赖，RAGAS 可选）
│   ├── dataset/queries.jsonl  12 条：5 现行 / 4 跨年陷阱 / 2 历史 / 1 无答案
│   ├── runner/                metrics · runner · report · cli · db · ragas_adapter
│   ├── thresholds.json        唯一权威阈值来源（PRD 与 runtime 参数都从这里读）
│   └── tests/                 56 例
│
└── infra/                     【F】基础设施
    ├── nginx/default.conf     反向代理（关缓冲保 SSE）+ SPA 转发
    ├── postgres/init/         001_schema.sql（与 data-model.md 逐字段一致）
    └── scripts/               wait-for · init-qdrant · reset-qdrant · test-init-qdrant.py
```

---

## 采集合规（强制）

1. 只抓公开页面，不使用任何登录态；
2. 域名白名单 `sgu.edu.cn`，越界即报错中止；
3. 单域串行，请求间隔 ≥1s；
4. 单次最多 **50 页列表页**（详情页与重试另计，总请求上限 800，硬顶 2000），**不递归整站**，
   只走"列表页 → 详情页"两跳；附件只记录链接、不下载正文（xlsx/pdf 需要额外解析与 OCR，
   且附件的时效性由正文条款决定）；
5. 不假设存在 API / RSS，采集层只有 HTML 解析一条实现路径；
6. 站点返回 403（WAF 拦截）时 `cli crawl` 以 **exit 4** 结束并打印处置指引 —— 这不是 bug，
   也**不允许**靠提高并发、缩短延时或轮换 UA 绕过。

详见 [`docs/prd.md §5.1`](docs/prd.md)。

---

## License

课程项目，仅用于教学与校内演示。抓取内容版权归韶关学院教务处所有。
