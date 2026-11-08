package handler

import (
	"context"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/sgu-notice-rag/api-server/internal/response"
)

// EvalSummary 处理 GET /api/v1/eval/summary（docs/api.md §5）。
// 数据由 E 跑批写入 Postgres，C 只读聚合结果。
func (h *Handler) EvalSummary(c *gin.Context) {
	runID := c.Query("run_id")

	ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
	defer cancel()

	summary, err := h.store.LatestEvalSummary(ctx, runID)
	if err != nil {
		if isDependencyDown(err) {
			response.Fail(c, http.StatusServiceUnavailable, response.CodeDependencyDown, "")
			return
		}
		response.Fail(c, http.StatusInternalServerError, response.CodeInternal, "")
		return
	}
	response.OK(c, summary)
}

// Healthz 处理 GET /healthz —— 存活探针（compose healthcheck 使用）。
// 语义：进程活着即 200；依赖状态放在 data.components 里，不因此改状态码
// （探针判死会导致 compose 重启循环，反而扩大故障面）。
func (h *Handler) Healthz(c *gin.Context) {
	components := map[string]string{}

	// Postgres
	if h.store != nil {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 800*time.Millisecond)
		if err := h.store.Ping(ctx); err != nil {
			components["postgres"] = "down"
		} else {
			components["postgres"] = "ok"
		}
		cancel()
	} else {
		components["postgres"] = "down"
	}

	// RAG Core
	ragStatus := "ok"
	if h.rag != nil {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 800*time.Millisecond)
		if _, err := h.rag.Health(ctx); err != nil {
			ragStatus = "down"
		}
		cancel()
	} else {
		ragStatus = "down"
	}
	components["rag_core"] = ragStatus

	// Redis（限流依赖；不可用时限流会降级，不算致命）
	if h.rdb != nil {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 500*time.Millisecond)
		if err := h.rdb.Ping(ctx).Err(); err != nil {
			components["redis"] = "down"
		} else {
			components["redis"] = "ok"
		}
		cancel()
	} else {
		components["redis"] = "down"
	}

	status := "ok"
	for _, v := range components {
		if v != "ok" {
			status = "degraded"
			break
		}
	}

	response.OK(c, gin.H{
		"status":     status,
		"components": components,
		"version":    h.version,
	})
}
