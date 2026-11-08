# ADR-001：后端 API 选择 Go + Gin，而非 FastAPI

- 状态：Accepted
- 日期：2026-11-08
- 决策者：C（Go API）、F（基础设施）
- 相关：`docs/architecture.md`、`references/tech-baseline.md`

## 背景

对外 REST 需要支撑三类请求：

1. 列表页分页查询（高频、轻计算、DB 密集）；
2. 问答请求（长耗时 0.8–2s，占住连接，需要大量并发挂起）；
3. **SSE 长连接**（单连接存活 5–30s，连接数远高于 QPS）。

学生演示场景会刻意压测"50 人同时提问"，即 50 个并发长连接挂在 SSE 上。这一条决定了我们真正要比较的不是"框架写起来爽不爽"，而是**每连接内存成本**与**协程调度在高并发挂起时的稳定性**。

## 决策

对外 API 采用 **Go 1.23 + Gin 1.10**。Python 只负责 RAG 核心（`rag-core/`），两者通过 HTTP 内部接口解耦。

## 依据

| 维度 | Go + Gin | FastAPI（uvicorn/asyncio） |
|---|---|---|
| 典型 QPS | **12,000–30,000** | 通常低一个数量级（同硬件、同 DB 往返） |
| 并发 1000 时吞吐 | 稳定 **~8,210 req/sec** | 依赖 async 实现质量，同步路由易退化 |
| 每连接内存 | goroutine 初始栈 ~2–8 KB | asyncio task + 框架栈，量级更高 |
| SSE 长连接 | 原生 `c.Writer.Flush()` + context 取消 | 需 `StreamingResponse`，取消传播需手动处理 |
| 部署体积 | ~15 MB 静态二进制 | 解释器 + 依赖，冷启动更慢 |
| 团队风险 | 需要 Go 基础 | 团队已写 Python |

**选 Go 的净理由**：SSE 长连接 + 列表高并发是本项目唯一有"数量级差异"的地方，恰好是 Go 的主场；而 RAG 的复杂逻辑（切块、embedding、rerank）仍然留在 Python 生态最强的位置。两者用 30 行内部 HTTP 客户端连接，耦合成本极低。

**接受 Go 的代价**：C 需要 1–2 天熟悉 Gin 的中间件链与 context 取消；如果 C 完全没有 Go 经验，则按 `references/tech-baseline.md` 降级到 FastAPI，**但必须在 PR 中记录 QPS 预期下降**，且 SSE 取消逻辑要手写。

## 后果

- 正向：SSE 组合 `http.Flusher` 与 `context.Context` 取消天然；二进制部署简单；JSON 序列化可预测。
- 负向：不能复用 Python 的 RAG 生态（本来也不需要，已解耦）；C 需要写两套错误处理（内部 HTTP + 对外 REST）。
- 约束：C **不得**直接连 Qdrant（见 `SKILL.md §6.2`），否则 Go 侧会重新引入 Python 生态的问题。

## 替代方案评估

| 方案 | 否决理由 |
|---|---|
| 全 FastAPI | SSE 取消与高并发挂起的实现风险高于收益；且与 B 同进程会破坏"B 不暴露 `/api/v1/*`"的边界 |
| 全 Go（连 RAG 也用 Go） | 评估生态（RAGAS）与 embedding 工具链在 Python；重写成本远超收益 |
| Node.js（Express/Fastify） | 团队无经验，无收益 |
| 直接上 Kubernetes + 网关 | 六人项目，运维成本是负资产；Compose + Nginx 已够 |
