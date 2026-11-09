# 六人启动指令（第 1 周的开工说明）

> 这份文件是**给六个人看的**，不是给评审看的。
> 读完你只需要知道三件事：**今天做什么**、**本周五交什么**、**什么情况下会被打回**。
>
> 元提示词要求的三条硬约束写在最后一节（§3）：改 API 契约前必须同步更新 Skill 的 references。

---

## 0. 所有人共用的第一步（今天内完成，约 40 分钟）

```bash
# 1) 取代码（只读主线，不要在 main 上改任何东西）
git clone <repo-url> sgu-notice-rag && cd sgu-notice-rag
git switch main && git pull

# 2) 跑一次自检，确认骨架在你机器上是好的
bash .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh   # 期望：检查通过
docker compose config --quiet                                      # 期望：无输出

# 3) 读三份文档（只读这三份，别的用到再查）
#    README.md          —— 怎么跑起来
#    docs/prd.md §3     —— 我们在拿什么指标验收
#    docs/api.md        —— 冻结的契约（改它要走 §3 的流程）

# 4) 让你的 Agent 加载 Skill —— 这一步不做，后面写的代码大概率与契约对不上
#    在 DSH / Claude 里说：加载 .claude/skills/sgu-notice-rag-dev/SKILL.md

# 5) 拉自己的分支（命名格式固定，CI 与 PR 模板都按这个认人）
git switch -c feat/A-scraper        # A
git switch -c feat/B-rag-core       # B
git switch -c feat/C-api            # C
git switch -c feat/D-frontend       # D
git switch -c feat/E-eval           # E
git switch -c feat/F-infra          # F
```

**为什么不能直接在 `main` 上改**：六条链路并行，`main` 上任何一次未评审的提交都会让另外五个人
拉到一半坏掉的骨架；而契约（`docs/api.md` + Skill 的 references）一旦在 `main` 上漂移，
`check-contract.sh` 会在六个人机器上同时变红，排查成本是单人的六倍。

---

## 1. 各人的第一周（每人 ≤ 25% 总工作量，各自独立验收）

### A —— 采集与 ETL（`ingestion/`）

| 项 | 内容 |
|---|---|
| **第一步** | 跑 `cd ingestion && python -m pytest -q`（应 72 passed），再跑 `python -m ingestion.cli crawl --max-pages 1 --dry-run`：**当前会以 exit 4 结束**，那是 WAF 拦截的既定信号（不是崩溃），提示文案会告诉你该怎么处置 |
| **本周五必交** | ① 站点 403 的**书面结论**（已实测：`www.sgu.edu.cn` 对非浏览器访问统一返回 `<title>block</title>`，见 `docs/deployment.md §8`）；② 若拿到放行，跑通真实抓取并产出 `ingestion/data/chunks.jsonl`（≥50 条通知）；③ 若拿不到放行，交出**用真实页面片段固化的回归集**（现在已有 21 例，扩到覆盖附件、跨年版本、无时效字段三类） |
| **不要做** | 不要为了绕过 403 去提高并发、缩短延时、轮换 UA 或引入 Playwright —— 这不是技术问题，是授权问题；也不要把 `requests`/`BeautifulSoup` 引进来，现在零依赖是刻意的（解析器必须能在无网容器里跑） |
| **验收命令** | `python -m pytest -q` && `python -m ingestion.cli check data/chunks.jsonl` |

### B —— 检索与 RAG 核心（`rag-core/`）

| 项 | 内容 |
|---|---|
| **第一步** | `cd rag-core && python -m pytest -q`（应 43 passed），再 `uvicorn app.main:app --port 8000` 起服，`curl -X POST localhost:8000/internal/retrieve -H 'Content-Type: application/json' -d '{"query":"转专业","time_filter":{"mode":"auto"}}'` |
| **本周五必交** | ① 把 `RAG_STUB_MODE=false` 下的真实 bge-m3 + reranker 接进来（`app/embedder.py` 的 `_real_*` 三个桩）；② `QdrantStore.hybrid_search` 打到真实 collection 上（现在 fixture 模式已可跑通全链路） |
| **不要做** | 不要动 `app/time_filter.py` 的三条既有行为（长期有效靠 `is_empty` 分支、降权在精排后截断前、`mode=off` 时 `applied_filters` 必须为空）—— 这三条有测试锁着，E 的评估口径依赖它们 |
| **验收命令** | `python -m pytest -q` && `ruff check app tests` |

### C —— Go 高并发 API（`api-server/`）

| 项 | 内容 |
|---|---|
| **第一步** | `cd api-server && go build ./... && go test ./...`（应全绿），再 `go run ./cmd/server` 起服，`curl localhost:8080/healthz` |
| **本周五必交** | ① 把 `internal/handler/answer.go` 的抽取式生成换成可插拔的生成器（`Handler.WithAnswerFunc` 已留好注入点，签名 `func(query string, chunks []client.Chunk) string`，返回空串会被降级成 `low_score` 拒答）；② `internal/store/postgres.go` 的 `ListNotices` 补全分页与筛选的真实 SQL（现在已实现，需与 `infra/postgres/init/001_schema.sql` 对齐复核） |
| **不要做** | 不要在 C 里连 Qdrant（C 的唯一下游是 `RAG_CORE_URL`）；不要改中间件顺序（`RequestID → Logger → Recovery → CORS → JWT → RateLimit → Handler`，JWT 必须早于限流，因为限流按 `sub` 计数） |
| **验收命令** | `go vet ./... && go test ./... -race && gofmt -l .`（最后一条必须无输出） |

### D —— Vue 3 前端（`frontend/`）

| 项 | 内容 |
|---|---|
| **第一步** | `cd frontend && pnpm install && pnpm dev` —— 默认 `VITE_USE_MOCK=true`，**零后端**就能看完整交互 |
| **本周五必交** | ① 通知列表虚拟滚动（现在 3 条 Mock，目标 1452 条不卡）；② SSE 断线重连与"停止生成"的状态收敛（`src/api/chat.ts` 的 `finished` 标志已处理单次回调，重连策略是空的）；③ 评估看板接真实 `/api/v1/eval/summary` |
| **不要做** | 不要硬编码后端地址（只认 `VITE_API_BASE_URL`）；不要自造字段（`src/api/__tests__/contract.spec.ts` 20 例逐字比对键集合）；`effective_to === null` 必须渲染成"**长期有效**"而不是"已过期" |
| **验收命令** | `pnpm typecheck && pnpm test && pnpm lint` |

### E —— 评估体系（`eval/`）

| 项 | 内容 |
|---|---|
| **第一步** | `cd eval && python -m pytest -q`（应 56 passed），`python -m runner.cli check` 看两条 `!` 告警的含义，`python -m runner.cli baseline` 看指标代码的真实输出 |
| **本周五必交** | **评估集从 12 条扩到 40 条**，其中无答案 ≥10、跨年度陷阱 ≥10 —— 现在 `check` 打的两条 `!` 告警就是这件事，不扩的话 §2 的 M1/M3 指标不具统计意义 |
| **不要做** | 不要为了好看把"问句点名旧版年份"的题标成陷阱题（`q07` 是 `historical` 不是 `cross_year_trap`）；不要硬编码阈值，唯一来源是 `eval/thresholds.json` |
| **验收命令** | `python -m pytest -q && ruff check runner tests && python -m runner.cli check` |

### F —— 基础设施（`infra/`）

| 项 | 内容 |
|---|---|
| **第一步** | `cp .env.example .env && docker compose up -d --build`，然后 `docker compose ps` 看 7 个常驻服务是否 running、`qdrant-init` 是否 `Exited (0)` |
| **本周五必交** | ① 干净机器上 **10 分钟内**从 clone 到浏览器可访问（`docs/deployment.md §2` 五步）；② CI 三绿（lint / test / build）；③ 首次启动把模型卷预下载打通（`docker compose --profile models run --rm model-puller`） |
| **不要做** | 不要改服务名与端口（`SKILL.md §6.1` 冻结）；不要给 `nginx` 挂 `./frontend/dist`（dist 是构建产物且已 gitignore，干净 clone 的机器上不存在）—— 前端是**独立容器**，nginx 只做转发 |
| **验收命令** | `docker compose config --quiet && docker compose up -d` 后逐项 `curl` |

---

## 2. 本周五的验收口径（六个人用同一套数字）

指标定义与目标写在 `docs/prd.md §3`，**不得各自发明**：

| 指标 | 目标 | 谁负责让它变好 | 谁来测 |
|---|---|---|---|
| `stale_recall_rate`（旧版本误召回率） | ≤ 15% | B（时间过滤 + 降权）、A（时效抽取准确度） | E |
| `refusal_rate`（无答案拒答率） | ≥ 0.90 | B（阈值）、C（拒答话术） | E |
| `faithfulness` | ≥ 0.85 | C（生成）、B（引用质量） | E（RAGAS） |
| `context_precision` | ≥ 0.70 | B | E（RAGAS） |
| p99 延迟 / 首 token | < 2s / < 1s | C、F | E（压测） |

**跑一次完整评估**（需要 api-server 已起）：

```bash
cd eval
python -m runner.cli run --endpoint http://localhost:8080 --write
# 报告落在 eval/report/<run_id>.md 与 <run_id>.json（逐条样本在 .samples.json）
```

---

## 3. 修改 API 契约前，必须同步更新 Skill 的 references（铁律）

契约有两份**必须同时**存在的副本，它们各自面向不同读者：

| 文件 | 读者 | 作用 |
|---|---|---|
| `docs/api.md` | 人（评审、新人） | 可读的完整契约 |
| `.claude/skills/sgu-notice-rag-dev/references/api-contract.md` | **Agent** | 开发时自动加载的约束副本 |

两者路径集合必须完全一致 —— `check-contract.sh` 第 3 段就是在比这个集合。

**任何一次契约变更（加字段、改类型、加端点、改错误码）都必须走完这五步**：

```bash
# 1) 先改 docs/api.md（人读的那份）
# 2) 同步改 references/api-contract.md，以及 —— 如果涉及数据模型 ——
#    references/data-model.md 与 docs/data-model.md
# 3) 跑自检，必须通过
bash .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh
# 4) 合约双方共同确认：谁改的谁找对面签字（C↔D 改 HTTP、A↔B 改 chunk 字段）
# 5) PR 标题必须以 contract-change: 开头，正文写明：改了什么、影响谁、谁确认的
```

**没有走这五步的契约变更，一律在评审时打回**，理由不是"流程没走"，而是：
`check-contract.sh` 在 CI 里是硬失败，契约半改不改会让另外五个人的分支同时红，
而红的原因看起来像"我的代码有问题"—— 这会浪费掉整组一天。

**唯一允许的例外**：只改文档措辞、不改任何字段名/类型/路径的 PR，可以不用 `contract-change:` 前缀。

---

## 4. 每人一句 Agent 提示词（复制即用）

> 用法：在自己的 Agent 里先加载 Skill，再贴这一句。它的作用是让 Agent **主动去核对**
> 你那部分的契约，而不是顺着你的想法往下写。

| 成员 | 提示词 |
|---|---|
| **A** | 我正在改采集模块。先读 `.claude/skills/sgu-notice-rag-dev/SKILL.md` §6 与 `ingestion/ingestion/config.py`，然后检查我的改动：`ALLOWED_DOMAINS` / `REQUEST_DELAY_SECONDS`(≥1) / `MAX_PAGES_PER_RUN` / `MAX_REQUESTS_PER_RUN` 四道闸门是否都还生效，有没有出现递归爬取或绕过 `Fetcher.get()` 的裸 `urllib` 调用。 |
| **B** | 我正在改 RAG 核心。先读 `docs/api.md §6.1`，然后核对 `POST /internal/retrieve` 的响应：顶层是否恰好 `chunks`/`scores`/`applied_filters`/`retrieve_ms` 四个键，`Chunk` 是否恰好 13 个字段且没有多余的打分字段，`mode=off` 时 `applied_filters` 是否为空数组。 |
| **C** | 我正在改 Go API。先读 `docs/api.md`，然后逐条核对我注册的路由：路径、HTTP 方法、是否挂了 JWT、限流 spec 名称、错误码，是否与契约表完全一致；并确认 `internal/client/ragcore.go` 是唯一访问 rag-core 的地方。 |
| **D** | 我正在改前端。先读 `docs/api.md` 与 `frontend/src/api/types.ts`，然后检查我的请求层：路径是否都在契约内、字段名是否与契约逐字一致、有没有硬编码后端地址、`VITE_USE_MOCK` 是否仍默认 `true`、`effective_to === null` 是否渲染成"长期有效"。 |
| **E** | 我正在改评估。先读 `eval/README.md` 与 `eval/thresholds.json`，然后检查我新增的 query：`query_id` 是否唯一、`category` 是否用了枚举值、`no_answer` 是否带 `expect_refusal:true`、`expected_doc_ids` 是否用了真实 `doc_id` 格式（`jwb-jxtz-{年}-{站点news_id}`）。 |
| **F** | 我正在改基础设施。先读 `docs/deployment.md §3`，然后检查 `docker-compose.yml`：7 个常驻服务名与端口是否与文档表一致、每个服务是否都有 healthcheck、`depends_on` 是否用了 `condition: service_healthy`、前端环境变量是否走 `build.args`（不是 `environment`）。 |

---

## 5. 卡住了怎么办（比问人更快）

| 现象 | 先看这里 |
|---|---|
| `check-contract.sh` 报 ✗ | 看是哪一段：路径不在契约里 → 你的路径写错了或契约没同步；字段缺失 → 字段名漂移 |
| `docker compose up` 后某服务不断重启 | `docs/deployment.md §5` 排障表，7 条按现象编排 |
| 采集拿到 403 且页面标题是 `block` | 这是 WAF，**不是 bug**；`docs/deployment.md §8`。不要重试、不要缩短延时 |
| 前端一片红但后端是好的 | 先确认 `VITE_USE_MOCK` —— 默认 `true` 时根本不发请求，红的是 Mock 数据或类型 |
| 测试在 CI 过、在本地不过 | 检查 Python 版本（CI 3.12）与 `PYTHONIOENCODING=utf-8`（Windows 默认 GBK，中文断言会挂） |
