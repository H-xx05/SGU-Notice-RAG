// Package config 【C】配置加载。所有变量只从环境变量读取，默认值与 .env.example 一致。
package config

import (
	"log"
	"os"
	"strconv"
	"time"
)

// Config 是 Go API 的全部运行期配置。
type Config struct {
	AppEnv         string
	APIPort        string
	JWTSecret      string
	JWTTTL         time.Duration
	RAGCoreURL     string
	RAGTimeout     time.Duration
	PostgresURL    string
	RedisURL       string
	AllowedOrigins []string // 开发期 CORS，生产走 Nginx 同源
}

// Load 读取环境变量并返回配置；缺失项使用开发默认值。
func Load() *Config {
	ttlHours := envInt("JWT_TTL_HOURS", 12)
	ragTimeout := envInt("RAG_TIMEOUT_SECONDS", 8)

	return &Config{
		AppEnv:         env("APP_ENV", "development"),
		APIPort:        env("API_PORT", "8080"),
		JWTSecret:      env("JWT_SECRET", "dev-change-me-in-production"),
		JWTTTL:         time.Duration(ttlHours) * time.Hour,
		RAGCoreURL:     env("RAG_CORE_URL", "http://rag-core:8000"),
		RAGTimeout:     time.Duration(ragTimeout) * time.Second,
		PostgresURL:    env("POSTGRES_URL", "postgres://sgu:sgu@postgres:5432/sgu?sslmode=disable"),
		RedisURL:       env("REDIS_URL", "redis://redis:6379/0"),
		AllowedOrigins: []string{"http://localhost:5173", "http://localhost:8088"},
	}
}

// IsProduction 用于关闭 /auth/token 等 dev-only 接口。
func (c *Config) IsProduction() bool { return c.AppEnv == "production" }

func env(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
		log.Printf("config: %s=%q 不是整数，回退到 %d", key, v, fallback)
	}
	return fallback
}
