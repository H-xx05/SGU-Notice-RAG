// Package middleware 【C】中间件链：请求 ID → 日志 → 恢复 → CORS → JWT → 限流。
// 顺序在 router.go 中固定，不得调换（JWT 必须在限流之前，限流按 sub 计数）。
package middleware

import (
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/sgu-notice-rag/api-server/internal/config"
	"github.com/sgu-notice-rag/api-server/internal/response"
)

// ---------- 请求 ID ----------

// RequestID 为每个请求生成/透传 X-Request-ID，便于日志串联。
func RequestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader("X-Request-ID")
		if id == "" {
			id = "req-" + time.Now().Format("20060102150405.000000") + "-" + randomSuffix()
		}
		c.Set("request_id", id)
		c.Header("X-Request-ID", id)
		c.Next()
	}
}

// ---------- 访问日志 ----------

// Logger 输出结构化访问日志（JSON 字段风格，单行）。
func Logger() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		latency := time.Since(start)
		sub, _ := c.Get("sub")
		log.Printf(`{"level":"info","request_id":%q,"method":%q,"path":%q,"status":%d,"latency_ms":%d,"sub":%q}`,
			c.GetString("request_id"), c.Request.Method, c.Request.URL.Path,
			c.Writer.Status(), latency.Milliseconds(), sub)
	}
}

// ---------- panic 恢复 ----------

// Recovery 捕获 panic，返回统一 50001 而不是 gin 默认的空响应。
func Recovery() gin.HandlerFunc {
	return func(c *gin.Context) {
		defer func() {
			if r := recover(); r != nil {
				log.Printf(`{"level":"error","request_id":%q,"panic":%v}`, c.GetString("request_id"), r)
				response.AbortFail(c, http.StatusInternalServerError, response.CodeInternal, "")
			}
		}()
		c.Next()
	}
}

// ---------- CORS（仅开发） ----------

// CORS 允许本地前端直连；生产同源由 Nginx 收敛，不需要它。
func CORS(cfg *config.Config) gin.HandlerFunc {
	allowed := make(map[string]bool, len(cfg.AllowedOrigins))
	for _, o := range cfg.AllowedOrigins {
		allowed[o] = true
	}
	return func(c *gin.Context) {
		origin := c.GetHeader("Origin")
		if origin != "" && (allowed[origin] || !cfg.IsProduction()) {
			c.Header("Access-Control-Allow-Origin", origin)
			c.Header("Vary", "Origin")
			c.Header("Access-Control-Allow-Credentials", "true")
			c.Header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Request-ID")
			c.Header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		}
		if c.Request.Method == http.MethodOptions {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}

// ---------- JWT ----------

const subKey = "sub"

// JWTAuth 校验 Bearer token，把 sub/role 写入 context。
// SSE 端点允许 ?token= 兜底（EventSource 不能设置请求头）。
func JWTAuth(cfg *config.Config, allowQueryToken bool) gin.HandlerFunc {
	return func(c *gin.Context) {
		raw := extractToken(c, allowQueryToken)
		if raw == "" {
			response.AbortFail(c, http.StatusUnauthorized, response.CodeUnauthorized, "缺少登录凭证")
			return
		}
		claims, err := ParseAndVerify(raw, cfg.JWTSecret)
		if err != nil {
			response.AbortFail(c, http.StatusUnauthorized, response.CodeUnauthorized, "")
			return
		}
		c.Set(subKey, claims.Subject)
		c.Set("role", claims.Role)
		c.Next()
	}
}

func extractToken(c *gin.Context, allowQuery bool) string {
	h := c.GetHeader("Authorization")
	if strings.HasPrefix(h, "Bearer ") {
		return strings.TrimSpace(strings.TrimPrefix(h, "Bearer "))
	}
	if allowQuery {
		return c.Query("token")
	}
	return ""
}

func randomSuffix() string {
	return strconvHex(time.Now().UnixNano() & 0xffffff)
}

func strconvHex(v int64) string {
	const hexdigits = "0123456789abcdef"
	if v == 0 {
		return "0"
	}
	buf := make([]byte, 0, 7)
	for v > 0 {
		buf = append([]byte{hexdigits[v&0xf]}, buf...)
		v >>= 4
	}
	return string(buf)
}
