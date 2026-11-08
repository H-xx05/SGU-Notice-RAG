// Command server 是 SGU-Notice-RAG 的 Go API 入口。【C】
//
// 启动顺序（与 docker-compose 的 depends_on 一致）：
//  1. 读配置
//  2. 连 Postgres（重试，最长 30s）——不可用则退出，避免半可用状态
//  3. 连 Redis（失败只降级限流，不阻止启动）
//  4. 建 RAG Core 客户端（惰性连接，不阻塞启动）
//  5. 注册路由，监听并优雅退出
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/sgu-notice-rag/api-server/internal/client"
	"github.com/sgu-notice-rag/api-server/internal/config"
	"github.com/sgu-notice-rag/api-server/internal/handler"
	"github.com/sgu-notice-rag/api-server/internal/middleware"
	"github.com/sgu-notice-rag/api-server/internal/router"
	"github.com/sgu-notice-rag/api-server/internal/store"
)

func main() {
	cfg := config.Load()
	log.Printf(`{"level":"info","msg":"starting api-server","env":%q,"port":%q}`, cfg.AppEnv, cfg.APIPort)

	// ---- Postgres ----
	st, err := openStoreWithRetry(cfg.PostgresURL, 30*time.Second)
	if err != nil {
		log.Printf(`{"level":"fatal","msg":"postgres 不可用","err":%q}`, err.Error())
		os.Exit(1)
	}
	defer func() { _ = st.Close() }()

	// ---- Redis（可选）----
	rdb := openRedis(cfg.RedisURL)
	if rdb == nil {
		log.Printf(`{"level":"warn","msg":"redis 不可用，限流降级为进程内令牌桶"}`)
	}

	// ---- 下游 ----
	ragClient := client.New(cfg.RAGCoreURL, cfg.RAGTimeout)

	limiter := middleware.NewLimiter(rdb)
	h := handler.New(cfg, st, ragClient, limiter, rdb)

	// 预留：接 LLM 答案生成时在此覆盖
	// h = h.WithAnswerFunc(func(q string, chunks []client.Chunk) string { ... })

	engine := router.New(cfg, h)

	srv := &http.Server{
		Addr:              ":" + cfg.APIPort,
		Handler:           engine,
		ReadHeaderTimeout: 5 * time.Second,
		// SSE 没有写超时限制（长连接）；其余端点靠 client 的 8s 预算兜底。
		WriteTimeout: 0,
		IdleTimeout:  120 * time.Second,
	}

	go func() {
		log.Printf(`{"level":"info","msg":"listening","addr":%q}`, srv.Addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf(`{"level":"fatal","msg":"listen failed","err":%q}`, err.Error())
			os.Exit(1)
		}
	}()

	// ---- 优雅退出 ----
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Printf(`{"level":"info","msg":"shutting down"}`)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Printf(`{"level":"error","msg":"shutdown","err":%q}`, err.Error())
	}
	if rdb != nil {
		_ = rdb.Close()
	}
	log.Printf(`{"level":"info","msg":"bye"}`)
}

// openStoreWithRetry 在 total 时长内反复尝试连接 Postgres。
// 为什么重试：compose 的 healthcheck 只保证容器 healthy，网络/初始化脚本可能还在跑。
func openStoreWithRetry(dsn string, total time.Duration) (*store.Store, error) {
	deadline := time.Now().Add(total)
	var lastErr error
	for attempt := 1; ; attempt++ {
		st, err := store.Open(dsn)
		if err == nil {
			return st, nil
		}
		lastErr = err
		if time.Now().After(deadline) {
			return nil, lastErr
		}
		wait := time.Duration(attempt) * 500 * time.Millisecond
		if wait > 4*time.Second {
			wait = 4 * time.Second
		}
		log.Printf(`{"level":"warn","msg":"postgres 连接失败，重试中","attempt":%d,"wait_ms":%d}`, attempt, wait.Milliseconds())
		time.Sleep(wait)
	}
}

// openRedis 失败返回 nil（限流器会自动降级）。
func openRedis(url string) *redis.Client {
	opt, err := redis.ParseURL(url)
	if err != nil {
		log.Printf(`{"level":"warn","msg":"REDIS_URL 解析失败","err":%q}`, err.Error())
		return nil
	}
	rdb := redis.NewClient(opt)

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		_ = rdb.Close()
		return nil
	}
	return rdb
}
