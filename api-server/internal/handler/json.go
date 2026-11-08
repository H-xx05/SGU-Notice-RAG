package handler

import (
	"encoding/json"
)

// mustJSON 序列化为 SSE 的 data 行；失败时退化为一个最小合法 JSON，
// 保证不会写出 "data: " 空行导致前端 JSON.parse 崩溃。
func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return `{"error":"marshal_failed"}`
	}
	return string(b)
}
