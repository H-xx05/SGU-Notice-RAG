// Package response 【C】统一响应包与错误码。
// 错误码表见 docs/api.md §7；新增码只能追加，不得改变既有语义。
package response

import (
	"encoding/json"
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/sgu-notice-rag/api-server/internal/model"
)

// 业务错误码（与 docs/api.md §7 一一对应，禁止改数值）
const (
	CodeOK             = 0
	CodeBadRequest     = 40001
	CodeUnauthorized   = 40101
	CodeForbidden      = 40301
	CodeNotFound       = 40401
	CodeRateLimited    = 42901
	CodeRAGUnavailable = 50201
	CodeRAGTimeout     = 50202
	CodeDependencyDown = 50301
	CodeInternal       = 50001
)

// Message 是各错误码的默认中文提示。
var Message = map[int]string{
	CodeOK:             "ok",
	CodeBadRequest:     "参数校验失败",
	CodeUnauthorized:   "登录凭证无效或已过期",
	CodeForbidden:      "该接口在当前环境不可用",
	CodeNotFound:       "资源不存在",
	CodeRateLimited:    "请求过于频繁，请稍后再试",
	CodeRAGUnavailable: "检索服务暂不可用",
	CodeRAGTimeout:     "检索服务响应超时",
	CodeDependencyDown: "依赖服务尚未就绪",
	CodeInternal:       "服务内部错误",
}

// JSON 输出统一响应包。
func JSON(c *gin.Context, httpStatus, code int, data any) {
	c.JSON(httpStatus, model.Envelope{
		Code:    code,
		Message: Message[code],
		Data:    mustMarshal(data),
	})
}

// OK 输出 200 + code 0。
func OK(c *gin.Context, data any) { JSON(c, http.StatusOK, CodeOK, data) }

// Fail 输出错误包，message 可覆盖默认文案（空串则用默认）。
func Fail(c *gin.Context, httpStatus, code int, message string) {
	msg := message
	if msg == "" {
		msg = Message[code]
	}
	c.JSON(httpStatus, model.Envelope{Code: code, Message: msg, Data: json.RawMessage("null")})
}

// AbortFail 输出错误包并终止中间件链。
func AbortFail(c *gin.Context, httpStatus, code int, message string) {
	Fail(c, httpStatus, code, message)
	c.Abort()
}

func mustMarshal(v any) json.RawMessage {
	if v == nil {
		return json.RawMessage("null")
	}
	b, err := json.Marshal(v)
	if err != nil {
		return json.RawMessage("null")
	}
	return b
}
