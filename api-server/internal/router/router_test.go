// Package router 的单元测试：验证路由表与契约一致（docs/api.md §1–§5）。
// 这些用例不需要 Postgres/Redis/Qdrant，可离线跑，是 CI 的第一道防线。
package router

import (
	"testing"

	"github.com/gin-gonic/gin"

	"github.com/sgu-notice-rag/api-server/internal/config"
	"github.com/sgu-notice-rag/api-server/internal/handler"
	"github.com/sgu-notice-rag/api-server/internal/middleware"
)

// contractRoutes 是 docs/api.md 冻结的对外路径集合。
// 新增/删除路径必须先改契约（SKILL.md §1），再改这里——两处不一致 CI 就红。
var contractRoutes = []struct {
	Method string
	Path   string
}{
	{"GET", "/healthz"},
	{"POST", "/api/v1/auth/token"},
	{"GET", "/api/v1/notices"},
	{"POST", "/api/v1/chat"},
	{"GET", "/api/v1/chat/stream"},
	{"GET", "/api/v1/eval/summary"},
}

func TestRouteTableMatchesContract(t *testing.T) {
	gin.SetMode(gin.TestMode)

	cfg := &config.Config{
		AppEnv:         "test",
		APIPort:        "0",
		JWTSecret:      "test-secret",
		AllowedOrigins: []string{"http://localhost"},
	}
	h := handler.New(cfg, nil, nil, middleware.NewLimiter(nil), nil)

	r := New(cfg, h)

	got := make(map[string]bool, len(r.Routes()))
	for _, route := range r.Routes() {
		got[route.Method+" "+route.Path] = true
	}

	for _, want := range contractRoutes {
		key := want.Method + " " + want.Path
		if !got[key] {
			t.Errorf("契约路径缺失: %s", key)
		}
	}

	if len(got) != len(contractRoutes) {
		t.Errorf("路由数量不一致：契约 %d 条，实际 %d 条", len(contractRoutes), len(got))
		for k := range got {
			t.Logf("实际路由: %s", k)
		}
	}
}
