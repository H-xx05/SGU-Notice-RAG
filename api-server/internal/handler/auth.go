package handler

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/sgu-notice-rag/api-server/internal/middleware"
	"github.com/sgu-notice-rag/api-server/internal/model"
	"github.com/sgu-notice-rag/api-server/internal/response"
)

// Token 处理 POST /api/v1/auth/token。
// 仅开发/演示：生产环境必须返回 40301（docs/api.md §1）。
func (h *Handler) Token(c *gin.Context) {
	if h.cfg.IsProduction() {
		response.Fail(c, http.StatusForbidden, response.CodeForbidden, "该接口在当前环境不可用")
		return
	}

	var req model.TokenRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.Fail(c, http.StatusBadRequest, response.CodeBadRequest, "user_id 不能为空")
		return
	}

	role := req.Role
	if role == "" {
		role = "student"
	}
	if role != "student" && role != "admin" {
		response.Fail(c, http.StatusBadRequest, response.CodeBadRequest, "role 只能是 student 或 admin")
		return
	}

	token, exp, err := middleware.Sign(req.UserID, role, h.cfg.JWTSecret, h.cfg.JWTTTL)
	if err != nil {
		response.Fail(c, http.StatusInternalServerError, response.CodeInternal, "")
		return
	}

	response.OK(c, model.TokenResponse{
		Token:     token,
		ExpiresAt: exp.Format("2006-01-02T15:04:05+08:00"),
	})
}
