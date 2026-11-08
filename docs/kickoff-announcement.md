# SGU-Notice-RAG 项目启动通知

各位，项目骨架已经落到仓库里，六个人的 Issue 也建好了。

**仓库**：https://github.com/A1muE4aY/SGU-Notice-RAG
**Issue 面板**：https://github.com/A1muE4aY/SGU-Notice-RAG/issues

## 先看三样东西

1. **`docs/kickoff.md`** —— 新人第一天先看这个。里面有每个人第一周该做什么、本周五怎么验收、卡住了查哪张表。
2. **`.claude/skills/sgu-notice-rag-dev/SKILL.md`** —— 用 Claude Code / Cursor 的同学先 `/skill` 加载它。它把我们冻结的契约、目录归属、时效性判定链路全写死了，Agent 会自动按它检查你的改动。
3. **你自己的那条 Issue**（下面有直达链接）—— 任务、验收标准、"不要做"清单都在里面。

## 六个人的 Issue

| 谁 | 模块 | Issue |
|---|---|---|
| **A** | 采集与 ETL（`ingestion/`） | [#1](https://github.com/A1muE4aY/SGU-Notice-RAG/issues/1) |
| **B** | 检索核心（`rag-core/`） | [#2](https://github.com/A1muE4aY/SGU-Notice-RAG/issues/2) |
| **C** | Go API（`api-server/`） | [#3](https://github.com/A1muE4aY/SGU-Notice-RAG/issues/3) |
| **D** | 前端（`frontend/`） | [#4](https://github.com/A1muE4aY/SGU-Notice-RAG/issues/4) |
| **E** | 评估（`eval/`） | [#5](https://github.com/A1muE4aY/SGU-Notice-RAG/issues/5) |
| **F** | 基础设施（`infra/`） | [#6](https://github.com/A1muE4aY/SGU-Notice-RAG/issues/6) |

## 你要做的第一件事（所有人一样）

```bash
git clone https://github.com/A1muE4aY/SGU-Notice-RAG.git
cd SGU-Notice-RAG

# 两分钟自查，确认环境和契约都对得上
sh .claude/skills/sgu-notice-rag-dev/scripts/check-contract.sh
docker compose config --quiet

# 拉自己的分支（别在 main 上改，main 有分支保护）
git checkout -b feat/A-scraper    # A 用这个
git checkout -b feat/B-rag-core   # B
git checkout -b feat/C-api        # C
git checkout -b feat/D-frontend   # D
git checkout -b feat/E-eval       # E
git checkout -b feat/F-infra      # F
```

## 为什么不能直接在 main 上改

六条链路是并行开发的，但**共用一份 API 契约**。任何一个人单方面改了接口形状，其余五个人的代码会在同一时刻全红——排查成本是单人出错的六倍。所以 main 开了保护：必须走 PR、必须一个 approve。

## 三条铁律

### 1. 改契约必须同步两份副本

契约有**两份**，内容必须一致：

- `docs/api.md` —— 给人看的
- `.claude/skills/sgu-notice-rag-dev/references/api-contract.md` —— 给 Agent 看的

改一份不改另一份，CI 的 `check-contract.sh` 会直接拦下。流程见 `CONTRIBUTING.md`，PR 标题以 `contract-change:` 开头，并且要 C↔D / A↔B 交叉确认。

### 2. 别把闸门调松

采集有四道合规闸门（域名白名单、请求间隔、列表页上限、总请求上限）。写错了不会报错，会被**压回合规区间**——这是故意的。

**官网现在对非浏览器访问返回 403**（WAF）。`cli crawl` 会以 exit 4 结束并打印处置指引。这不是 bug，也**不允许**靠提高并发、缩短延时或轮换 UA 绕过。

### 3. `effective_to === null` 是「长期有效」，不是「已过期」

这是本产品的核心语义。《韶关学院学生学籍管理规定》没有截止日期，意思是**一直有效**。把它显示成"已过期"是比不显示更严重的错误——用户会以为规章废了。

## 骨架现在是什么状态

不是空壳，是能跑的最小闭环：

| 模块 | 状态 | 自测命令 |
|---|---|---|
| `ingestion/` | 72 passed，**零第三方依赖** | `cd ingestion && python -m pytest -q` |
| `rag-core/` | 43 passed，fixture 语料能跑完整时效链路 | `cd rag-core && python -m pytest -q` |
| `api-server/` | 6 条路由全注册，JWT + 限流链完整 | `cd api-server && go build ./... && go test ./...` |
| `frontend/` | 28 passed，**默认 Mock 零后端可跑** | `cd frontend && pnpm install && pnpm dev` |
| `eval/` | 56 passed，`check` + `baseline` 都 exit 0 | `cd eval && python -m runner.cli check` |
| `infra/` | `docker compose config` 通过 | `docker compose up -d --build` |

前端同学注意：`pnpm dev` 默认是 Mock 模式，不需要等后端。

## 本周五验收

指标（写在 `docs/prd.md §3`，数字的唯一权威来源是 `eval/thresholds.json`）：

| 指标 | 目标 | 谁负责 |
|---|---|---|
| 旧版本误召回率 | ≤ 15% | B + E |
| 拒答准确率 | ≥ 0.90 | B + C |
| Faithfulness | ≥ 0.85 | B + E |
| Context Precision | ≥ 0.70 | B |
| p99 延迟 / 首 token | < 2s / < 1s | C + F |

跑批命令：

```bash
cd eval
python -m runner.cli run --endpoint http://localhost:8080 --write
```

## 卡住了怎么办

先看 `docs/kickoff.md §5` 的对照表（五条常见现象 → 先看哪里）。还是没有的话，在对应 Issue 里留言，或者直接找我。

另外提一句：CI 现在是绿的（三 job：lint / test / build）。如果它红了，先看是不是自己本地没跑过对应的测试——大概率是同一件事。

---

准备好了就开工。有疑问随时提。

**项目地址**：https://github.com/A1muE4aY/SGU-Notice-RAG
