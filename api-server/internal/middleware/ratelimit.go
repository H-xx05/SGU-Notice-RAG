package middleware

import (
	"context"
	"fmt"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"

	"github.com/sgu-notice-rag/api-server/internal/response"
)

// RateLimitSpec 单个路由的配额：每分钟 requestsPerMinute 次，允许突发 burst。
type RateLimitSpec struct {
	Route             string
	RequestsPerMinute int
	Burst             int
}

// 配额表（与 docs/api.md §8 一致，改数值 = 契约变更）
var DefaultRateLimits = map[string]RateLimitSpec{
	"notices":      {Route: "notices", RequestsPerMinute: 300, Burst: 60},
	"chat":         {Route: "chat", RequestsPerMinute: 30, Burst: 10},
	"chat_stream":  {Route: "chat_stream", RequestsPerMinute: 20, Burst: 5},
	"eval_summary": {Route: "eval_summary", RequestsPerMinute: 60, Burst: 10},
	"auth_token":   {Route: "auth_token", RequestsPerMinute: 10, Burst: 5},
}

// Limiter 是令牌桶实现：优先 Redis（多实例共享），Redis 不可用时降级为进程内桶。
// 降级时**放行**而不是拒绝——限流是保护手段，不是业务规则。
type Limiter struct {
	rdb     *redis.Client
	mu      sync.Mutex
	local   map[string]*bucket
	enabled bool
}

type bucket struct {
	tokens float64
	last   time.Time
}

// NewLimiter 创建限流器；rdb 为 nil 时只走进程内桶。
func NewLimiter(rdb *redis.Client) *Limiter {
	return &Limiter{rdb: rdb, local: make(map[string]*bucket), enabled: true}
}

// Disable 关闭限流（单元测试用）。
func (l *Limiter) Disable() { l.enabled = false }

// Middleware 返回绑定到某个配额的 gin 中间件。
func (l *Limiter) Middleware(spec RateLimitSpec) gin.HandlerFunc {
	refillPerSec := float64(spec.RequestsPerMinute) / 60.0
	capacity := float64(spec.RequestsPerMinute + spec.Burst)

	return func(c *gin.Context) {
		if !l.enabled {
			c.Next()
			return
		}
		sub := c.GetString("sub")
		if sub == "" {
			sub = c.ClientIP() // 未鉴权路由（notices/auth）按 IP 计数
		}
		key := "rl:" + spec.Route + ":" + sub

		allowed, retryAfter := l.allow(c.Request.Context(), key, capacity, refillPerSec)
		if !allowed {
			c.Header("Retry-After", strconv.Itoa(int(retryAfter.Seconds())+1))
			response.AbortFail(c, http.StatusTooManyRequests, response.CodeRateLimited, "")
			return
		}
		c.Next()
	}
}

func (l *Limiter) allow(ctx context.Context, key string, capacity, refillPerSec float64) (bool, time.Duration) {
	now := time.Now()

	// 优先 Redis：INCR + EXPIRE 的固定窗口会误伤突发，这里用 Lua 做令牌桶。
	if l.rdb != nil {
		ok, wait, err := l.allowRedis(ctx, key, capacity, refillPerSec, now)
		if err == nil {
			return ok, wait
		}
		// Redis 出错 → 降级到本地桶，不阻塞请求
	}

	return l.allowLocal(key, capacity, refillPerSec, now), 0
}

var tokenBucketLua = redis.NewScript(`
local key   = KEYS[1]
local cap   = tonumber(ARGV[1])
local rate  = tonumber(ARGV[2])
local now   = tonumber(ARGV[3])
local ttl   = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts     = tonumber(data[2])
if tokens == nil then tokens = cap; ts = now end

local delta = math.max(0, now - ts)
tokens = math.min(cap, tokens + delta * rate)

local allowed = 0
local retry_ms = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_ms = math.ceil((1 - tokens) / rate * 1000)
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, ttl)
return {allowed, retry_ms}
`)

func (l *Limiter) allowRedis(ctx context.Context, key string, capacity, refillPerSec float64, now time.Time) (bool, time.Duration, error) {
	callCtx, cancel := context.WithTimeout(ctx, 200*time.Millisecond)
	defer cancel()

	res, err := tokenBucketLua.Run(callCtx, l.rdb, []string{key},
		capacity, refillPerSec, float64(now.UnixMilli()), 60000).Slice()
	if err != nil {
		return false, 0, err
	}
	if len(res) != 2 {
		return false, 0, fmt.Errorf("unexpected lua result length %d", len(res))
	}
	allowed, _ := res[0].(int64)
	retryMS, _ := res[1].(int64)
	return allowed == 1, time.Duration(retryMS) * time.Millisecond, nil
}

func (l *Limiter) allowLocal(key string, capacity, refillPerSec float64, now time.Time) bool {
	l.mu.Lock()
	defer l.mu.Unlock()

	b, ok := l.local[key]
	if !ok {
		b = &bucket{tokens: capacity, last: now}
		l.local[key] = b
	}
	elapsed := now.Sub(b.last).Seconds()
	b.tokens = minF(capacity, b.tokens+elapsed*refillPerSec)
	b.last = now
	if b.tokens >= 1 {
		b.tokens--
		return true
	}
	return false
}

func minF(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
