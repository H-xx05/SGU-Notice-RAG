// Package router 【C】路由注册与中间件链。
//
// 中间件顺序在本文件固定，不得调换（SKILL.md §3、middleware 包注释）：
//
//	RequestID → Logger → Recovery → CORS → [JWTAuth] → [RateLimit] → Handler
//
// JWT 必须先于限流：限流按 `sub` 计数，未鉴权时只能退化为按 IP。
package router

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/sgu-notice-rag/api-server/internal/config"
	"github.com/sgu-notice-rag/api-server/internal/handler"
	"github.com/sgu-notice-rag/api-server/internal/middleware"
	"github.com/sgu-notice-rag/api-server/internal/response"
)

// New 构建 gin.Engine。
func New(cfg *config.Config, h *handler.Handler) *gin.Engine {
	if cfg.IsProduction() {
		gin.SetMode(gin.ReleaseMode)
	}

	r := gin.New()
	r.RedirectTrailingSlash = false
	r.HandleMethodNotAllowed = true

	// 1) 全局链
	r.Use(middleware.RequestID())
	r.Use(middleware.Logger())
	r.Use(middleware.Recovery())
	r.Use(middleware.CORS(cfg))

	// 2) 未匹配路由：统一 404 包，避免 gin 默认空响应
	r.NoRoute(func(c *gin.Context) {
		response.Fail(c, http.StatusNotFound, response.CodeNotFound, "")
	})
	r.NoMethod(func(c *gin.Context) {
		response.Fail(c, http.StatusMethodNotAllowed, response.CodeBadRequest, "方法不允许")
	})

	limiter := h.Limiter()
	auth := middleware.JWTAuth(cfg, false)
	authQuery := middleware.JWTAuth(cfg, true) // SSE 允许 ?token=

	// 3) 公开端点
	r.GET("/healthz", h.Healthz)

	// 4) /api/v1
	v1 := r.Group("/api/v1")
	{
		// auth：公开但严格限流（防刷 token）
		v1.POST("/auth/token",
			limiter.Middleware(middleware.DefaultRateLimits["auth_token"]),
			h.Token,
		)

		// notices：公开（列表页不需要登录），按 IP 限流
		v1.GET("/notices",
			limiter.Middleware(middleware.DefaultRateLimits["notices"]),
			h.Notices,
		)

		// chat：JWT + 限流
		v1.POST("/chat",
			auth,
			limiter.Middleware(middleware.DefaultRateLimits["chat"]),
			h.Chat,
		)

		// chat/stream：JWT（?token= 兜底）+ 限流
		v1.GET("/chat/stream",
			authQuery,
			limiter.Middleware(middleware.DefaultRateLimits["chat_stream"]),
			h.ChatStream,
		)

		// eval/summary：JWT + 限流
		v1.GET("/eval/summary",
			auth,
			limiter.Middleware(middleware.DefaultRateLimits["eval_summary"]),
			h.EvalSummary,
		)
	}

	return r
}
