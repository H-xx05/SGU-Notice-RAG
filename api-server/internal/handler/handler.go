// Package handler 【C】对外 HTTP Handler。
// 每个 Handler 只做三件事：解析入参 → 调用下游（rag-core / Postgres）→ 组装对外响应。
// 业务事实（检索、时效判定）一律在 B；C 不复制任何判定逻辑（SKILL.md §3.1）。
package handler

import (
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/sgu-notice-rag/api-server/internal/client"
	"github.com/sgu-notice-rag/api-server/internal/config"
	"github.com/sgu-notice-rag/api-server/internal/middleware"
	"github.com/sgu-notice-rag/api-server/internal/store"
)

// RefusalAnswer 是拒答固定文案，与 docs/api.md §3 逐字一致；改动 = 契约变更。
const RefusalAnswer = "未在教学通知中找到可回答该问题的现行依据。" +
	"可能原因：该事项本年度未发布通知，或相关通知已过期。建议直接查阅教务处网站。"

// 拒答原因枚举（docs/api.md §3）。
const (
	RefuseNoHit    = "no_hit"
	RefuseAllStale = "all_stale"
	RefuseLowScore = "low_score"
)

// MinAnswerScore 是"有依据可答"的最低 rerank 分。
// 预留：该阈值应由 E 用 eval 集标定后经 .env 的 ANSWER_MIN_SCORE 注入
// （参数调整不算契约变更，C 补齐配置读取即可，勿改判定顺序）。
const MinAnswerScore = 0.35

// Handler 聚合所有依赖。新增依赖走 With* 可选注入，避免反复改构造函数签名。
type Handler struct {
	cfg     *config.Config
	store   *store.Store
	rag     *client.Client
	limiter *middleware.Limiter
	rdb     *redis.Client

	// answerFn 是答案生成注入点。默认抽取式模板（不依赖外部 LLM）；
	// 接 LLM 时在 main.go 用 WithAnswerFunc 覆盖。返回空串 = 生成失败。
	answerFn func(query string, chunks []client.Chunk) string

	version string

	now func() time.Time
}

// New 构造 Handler。
func New(cfg *config.Config, st *store.Store, rag *client.Client, limiter *middleware.Limiter, rdb *redis.Client) *Handler {
	h := &Handler{
		cfg:     cfg,
		store:   st,
		rag:     rag,
		limiter: limiter,
		rdb:     rdb,
		version: "0.1.0",
		now:     time.Now,
	}
	h.answerFn = extractiveAnswer
	return h
}

// WithAnswerFunc 覆盖答案生成实现（接 LLM 时使用）。
func (h *Handler) WithAnswerFunc(fn func(query string, chunks []client.Chunk) string) *Handler {
	if fn != nil {
		h.answerFn = fn
	}
	return h
}

// Limiter 暴露给 router 注册限流中间件（限流器由 main 构造后注入）。
func (h *Handler) Limiter() *middleware.Limiter { return h.limiter }

// today 返回 Asia/Shanghai 时区的今天（YYYY-MM-DD）。
// 容器已设 TZ=Asia/Shanghai；这里再显式固定一次，避免宿主机时区污染评估口径。
func (h *Handler) today() string { return shanghai(h.now()).Format("2006-01-02") }

// nowRFC3339 返回 RFC3339 时刻（带 +08:00 偏移）。
func (h *Handler) nowRFC3339() string { return shanghai(h.now()).Format("2006-01-02T15:04:05+08:00") }

// shanghai 把任意时刻转换到 Asia/Shanghai；tzdata 缺失时退回固定 +08:00。
func shanghai(t time.Time) time.Time {
	loc, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		loc = time.FixedZone("CST", 8*3600)
	}
	return t.In(loc)
}

func strPtr(s string) *string { return &s }
