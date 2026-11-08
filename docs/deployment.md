# 部署与运行（F 编写，A—E 必须在本机跑通）

> 目标：**干净机器 10 分钟内**从 clone 到浏览器可访问。
> 本文档是**唯一**的启动口径；README 只做入口。

---

## 1. 前置要求

| 工具 | 版本 | 检查命令 |
|---|---|---|
| Docker Engine | ≥ 24 | `docker --version` |
| Docker Compose | v2 | `docker compose version` |
| Git | ≥ 2.30 | `git --version` |
| （可选）Go | 1.23 | `go version`（仅本地跑 C 不带容器时需要） |
| （可选）Node | ≥ 20 | `node -v`（仅本地跑 D 时需要） |
| （可选）Python | 3.12 | `python -V`（仅本地跑 A/B/E 时需要） |

磁盘：镜像约 3.5 GB（含 qdrant、postgres、redis、python 依赖）；内存建议 ≥ 8 GB（bge-m3 推理占 ~2.5 GB）。

---

## 2. 从零到可访问（五步）

```bash
# 1) 取代码
git clone <repo-url> sgu-notice-rag && cd sgu-notice-rag

# 2) 生成配置
cp .env.example .env
#    默认值即可跑通；如需改端口/密钥，编辑 .env

# 3) 拉取模型（首次约 2.3 GB，建议先跑；失败可跳过，rag-core 会以 stub 模式启动）
docker compose --profile models run --rm model-puller

# 4) 启动
docker compose up -d --build

# 5) 验证
curl -s http://localhost:8088/api/v1/notices?page=1\&page_size=1 | head -c 300; echo
curl -s http://localhost:8088/healthz
```

浏览器打开 <http://localhost:8088> 应看到通知列表页。

> **首次启动约 2–5 分钟**：Go 编译 + Python 依赖 + Postgres 初始化。
> 起不来先看 §5 排障表。

---

## 3. 服务清单与端口

| 服务名 | 容器内端口 | 宿主机端口 | 镜像 | 健康检查 | 说明 |
|---|---|---|---|---|---|
| `nginx` | 80 | **8088** | nginx:1.27-alpine | `GET /healthz` | **统一入口** |
| `frontend` | 80 | 5173 | 本地构建 | `GET /` | dev 模式直连（`VITE_USE_MOCK=true`） |
| `api-server` | 8080 | 8080 | 本地构建（Go） | `GET /healthz` | 对外 REST |
| `rag-core` | 8000 | 8000 | 本地构建（Python） | `GET /internal/health` | **只在内网** |
| `qdrant` | 6333 / 6334 | 6333 | qdrant/qdrant:v1.12 | `GET /healthz` | 向量库 |
| `postgres` | 5432 | 5432 | postgres:16-alpine | `pg_isready` | 关系库 |
| `redis` | 6379 | 6379 | redis:7-alpine | `redis-cli ping` | 限流 |

**冻结项**：服务名与端口不可改（`SKILL.md §6.1`）。新增依赖先找 F。

### 3.1 Nginx 路由规则

```
/                 → frontend:80        （前端容器自带 nginx + try_files 回落）
/api/             → api-server:8080    （不剥前缀，Go 侧带 /api/v1）
/healthz          → api-server:8080/healthz
/internal/        → 404                 （内部接口不出网）
```

SSE 必须的配置（`infra/nginx/default.conf`）：

```nginx
location /api/ {
    proxy_pass http://api-server:8080;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
    chunked_transfer_encoding on;
}
```

---

## 4. 环境变量

从 `.env.example` 复制。**所有变量都在这一个文件**，不要在别处写死。

共 **29 项**，按作用域排列。四道采集合规闸门在最后一段，**改动它们等于改合规边界**。

| 变量 | 默认 | 作用域 | 含义 |
|---|---|---|---|
| `APP_ENV` | `development` | C | `production` 时关闭 `/auth/token` |
| `TZ` | `Asia/Shanghai` | 全部 | 容器时区，只影响日志时间戳；**时效判定用 `APP_TIMEZONE`，不读它** |
| `API_PORT` | `8080` | C | Go 监听端口 |
| `JWT_SECRET` | `dev-change-me` | C | HS256 密钥，**生产必改** |
| `JWT_TTL_HOURS` | `12` | C | token 有效期 |
| `RAG_CORE_URL` | `http://rag-core:8000` | C | RAG 核心地址（**C 的唯一下游**） |
| `RAG_TIMEOUT_SECONDS` | `8` | C | 内部调用超时 |
| `POSTGRES_URL` | `postgres://sgu:sgu@postgres:5432/sgu?sslmode=disable` | A/C/E | 关系库 DSN。E 也认 `DATABASE_DSN` / `POSTGRES_DSN` |
| `POSTGRES_USER` | `sgu` | 容器初始化 | 仅首次创建数据卷时生效 |
| `POSTGRES_PASSWORD` | `sgu` | 容器初始化 | 同上，**生产必改** |
| `POSTGRES_DB` | `sgu` | 容器初始化 | 同上 |
| `REDIS_URL` | `redis://redis:6379/0` | C | 限流 |
| `VECTOR_DB_URL` | `http://qdrant:6333` | B/A | Qdrant 地址 |
| `QDRANT_COLLECTION` | `sgu_notices` | A/B | 集合名 |
| `EMBED_MODEL` | `BAAI/bge-m3` | B | 嵌入模型 |
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | B | 重排模型 |
| `RAG_TOP_K` | `20` | B | 召回后送入 rerank 的条数（别名 `RERANK_TOP_K`） |
| `RAG_STUB_MODE` | `false` | B | `true` 时不加载模型，用确定性假向量跑通链路（**仅开发与 CI**，答案质量无意义） |
| `SCRAPER_ALLOWED_DOMAINS` | `sgu.edu.cn` | A | 域名白名单（逗号分隔），**只接受默认白名单的子集** |
| `SCRAPER_REQUEST_DELAY_SECONDS` | `1.0` | A | 请求间隔。**下限 1**，更低的值被压回 1（别名 `SCRAPER_DELAY_SECONDS`） |
| `SCRAPER_MAX_PAGES_PER_RUN` | `50` | A | 单次**列表页**上限，不是总请求数（别名 `SCRAPER_MAX_PAGES`） |
| `SCRAPER_MAX_REQUESTS_PER_RUN` | `800` | A | 单次 HTTP 请求总数（含详情页与重试），硬顶 2000 |
| `SCRAPER_ENTRY_URL` | `https://www.sgu.edu.cn/jwb/jxtz.htm` | A | 唯一入口，锁定单栏目，不递归 |
| `SCRAPER_USER_AGENT` | `SGU-Notice-RAG/dev (student project)` | A | 标识自己，不做伪装 |
| `INGESTION_DATA_DIR` | `ingestion/data` | A | `chunks.jsonl` 与原始 HTML 的落地目录 |
| `VITE_USE_MOCK` | `true` | D | `true` 时前端走 Mock，零后端 |
| `VITE_API_BASE_URL` | `/api` | D | **不得**写死 `http://localhost:8080` |
| `EVAL_DATASET` | `eval/dataset/queries.jsonl` | E | 评估集路径 |
| `EVAL_OUTPUT_DIR` | `eval/report` | E | 跑批报告落地目录 |

> **变量名冻结**（README 配置表列的就是这五个）：`RAG_CORE_URL`、`VECTOR_DB_URL`、`SCRAPER_MAX_PAGES`、`SCRAPER_DELAY_SECONDS`、`VITE_USE_MOCK`。
> 其中后两个是 `SCRAPER_MAX_PAGES_PER_RUN` / `SCRAPER_REQUEST_DELAY_SECONDS` 的**短别名**，`ingestion/ingestion/config.py` 两种拼法都读（正式名优先）。保留别名是为了不让早先写好的 `.env` 静默失效 —— 变量名写错等于闸门失效，这是最危险的一类配置错误。

---

## 5. 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| `api-server` 反复重启 | 等不到 postgres | `docker compose logs api-server`；确认 postgres healthy |
| `/healthz` 502 | api-server 未起 | `docker compose ps` 看状态；端口占用 → 改 `.env` 的 `API_PORT` |
| SSE 只出 `meta` 就断 | Nginx 缓冲 | 确认 `proxy_buffering off`、`X-Accel-Buffering: no` |
| `rag-core` 启动即退出 | 模型未下载 + 无网 | 设置 `RAG_STUB_MODE=true` 用假向量跑通链路 |
| 前端白屏 | `VITE_USE_MOCK=false` 但 API 未起 | 改回 `true` 或先起后端 |
| 采集 403 / 页面标题是 `block` | 官网 WAF 拦截非浏览器请求（**已实测，见 §排障 8**） | 不要加重试、不要缩短延时；改用浏览器手动确认或走校内授权渠道 |
| 采集超时（非 403） | 网络抖动或对方限速 | 提高 `SCRAPER_REQUEST_DELAY_SECONDS`，降低 `SCRAPER_MAX_PAGES_PER_RUN` |
| 端口被占用 | 本机已有服务 | 改 `.env` 的宿主机端口（nginx 8088 / api 8080） |
| `cli crawl` 退出码 4 | 判定为 WAF 拦截（`BlockedError`） | 见下方第 8 条，**这不是代码 bug** |

### 8. 关于官网 403（已实测，2026-11）

`https://www.sgu.edu.cn/**` 对非浏览器发起的请求统一返回 **HTTP 403 + `<title>block</title>`** 的拦截页
（约 47 KB，页面里没有任何通知链接）。自定义 UA、完整浏览器 UA + Accept 头、curl、首页/栏目页/列表页
逐一验证，**全部 403**。

因此：

- `ingestion` 的 `Fetcher` 把它识别为 `BlockedError`（**不是** `FetchError`），`cli crawl` 以此退出码 **4** 结束，
  并打印处置指引。两者分开是因为处置方式完全不同：解析失败要改代码，被拦截要改渠道。
- 解析器、时效抽取、切块的正确性靠 `ingestion/tests/` 里**真实页面片段**做离线回归（72 例，无需联网）。
- **不要**通过提高并发、缩短延时、轮换 UA 来"绕过"—— 那是在把项目风险转嫁给学校。
  正确做法：浏览器里确认放行后人工取样本、申请校内授权接口、或改用教务系统导出的通知清单。

---

## 6. 本地开发（不用容器跑某一条链路）

```bash
# C：Go API（需 postgres/redis/rag-core 已起）
cd api-server && go run ./cmd/server

# D：前端 Mock 模式（不依赖任何后端）
cd frontend && pnpm install && VITE_USE_MOCK=true pnpm dev

# B：RAG Core
cd rag-core && python -m venv .venv && . .venv/bin/activate && pip install -e . && uvicorn app.main:app --reload --port 8000

# A：采集（写 chunks.jsonl 到 ingestion/data/）
cd ingestion && pip install -e . && python -m ingestion.cli crawl --max-pages 3 --dry-run

# E：评估
cd eval && pip install -e . && python -m eval.runner --dataset dataset/queries.jsonl
```

---

## 7. CI（F 负责）

`.github/workflows/ci.yml`，三个 job：

| job | 触发 | 内容 | 失败即阻塞合并 |
|---|---|---|---|
| `lint` | push/PR | `gofmt -l`、`go vet`、`ruff`、`pnpm lint`、`bash check-contract.sh` | 是 |
| `test` | PR | `go test ./...`、`pytest`、`pnpm test` | 是 |
| `build` | PR | `docker compose build`（不启动） | 是 |

> `check-contract.sh` 失败 = 契约被破坏，**不允许**用 `--no-verify` 绕过。

---

## 8. 从零到可访问的验收清单（F 自查）

- [ ] `docker compose up -d --build` 后 7 个容器全部 `running`
- [ ] `curl localhost:8088/healthz` 返回 `{"code":0,...}`
- [ ] `curl "localhost:8088/api/v1/notices?page=1&page_size=1"` 返回 `code:0`
- [ ] 浏览器打开 `localhost:8088` 看到列表页
- [ ] `curl -N "localhost:8088/api/v1/chat/stream?q=test&token=<jwt>"` 依次收到 `meta`/`sources`/`delta`/`done`
- [ ] `docker compose down && docker compose up -d` 数据不丢（volume 生效）
