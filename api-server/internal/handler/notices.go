package handler

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/sgu-notice-rag/api-server/internal/response"
	"github.com/sgu-notice-rag/api-server/internal/store"
)

// Notices 处理 GET /api/v1/notices（docs/api.md §2）。
// 公开接口，数据来自 Postgres（A 写入，C 只读）。
func (h *Handler) Notices(c *gin.Context) {
	filter, err := parseNoticeFilter(c)
	if err != nil {
		response.Fail(c, http.StatusBadRequest, response.CodeBadRequest, err.Error())
		return
	}

	page, err := h.store.ListNotices(c.Request.Context(), filter)
	if err != nil {
		// 依赖不可用归 50301，其它归 50001——两者前端处理不同（降级态 vs 错误页）。
		if isDependencyDown(err) {
			response.Fail(c, http.StatusServiceUnavailable, response.CodeDependencyDown, "")
			return
		}
		response.Fail(c, http.StatusInternalServerError, response.CodeInternal, "")
		return
	}

	response.OK(c, page)
}

// parseNoticeFilter 校验 query 参数；任何越界都返回 40001（docs/api.md §0.4）。
func parseNoticeFilter(c *gin.Context) (store.NoticeFilter, error) {
	f := store.NoticeFilter{
		Page:         intParam(c, "page", 1),
		PageSize:     intParam(c, "page_size", 20),
		Year:         intParam(c, "year", 0),
		Q:            strings.TrimSpace(c.Query("q")),
		PublishFrom:  strings.TrimSpace(c.Query("publish_from")),
		PublishTo:    strings.TrimSpace(c.Query("publish_to")),
		ContentType:  strings.TrimSpace(c.Query("content_type")),
		DocVersion:   strings.TrimSpace(c.Query("doc_version")),
		IncludeStale: boolParam(c, "include_stale", true),
	}

	if f.Page < 1 {
		return f, errBadParam("page 必须 ≥ 1")
	}
	if f.PageSize < 1 || f.PageSize > 100 {
		return f, errBadParam("page_size 必须在 1–100 之间")
	}
	if f.Year != 0 && (f.Year < 2000 || f.Year > 2100) {
		return f, errBadParam("year 不在合理范围")
	}
	for _, d := range []struct{ name, val string }{
		{"publish_from", f.PublishFrom},
		{"publish_to", f.PublishTo},
	} {
		if d.val != "" && !isDate(d.val) {
			return f, errBadParam(d.name + " 必须是 YYYY-MM-DD")
		}
	}
	if f.PublishFrom != "" && f.PublishTo != "" && f.PublishFrom > f.PublishTo {
		return f, errBadParam("publish_from 不能晚于 publish_to")
	}
	if f.ContentType != "" && !validContentType(f.ContentType) {
		return f, errBadParam("content_type 只能是 notice_body|attachment|table|appendix")
	}
	return f, nil
}

func validContentType(v string) bool {
	switch v {
	case "notice_body", "attachment", "table", "appendix":
		return true
	}
	return false
}

func intParam(c *gin.Context, key string, fallback int) int {
	raw := strings.TrimSpace(c.Query(key))
	if raw == "" {
		return fallback
	}
	n, err := strconv.Atoi(raw)
	if err != nil {
		return -1 // 让调用方的范围校验统一报错，避免两处口径不一致
	}
	return n
}

func boolParam(c *gin.Context, key string, fallback bool) bool {
	raw := strings.TrimSpace(c.Query(key))
	if raw == "" {
		return fallback
	}
	b, err := strconv.ParseBool(raw)
	if err != nil {
		return fallback
	}
	return b
}

// isDate 只做格式校验（不校验真实存在的日期，A 侧入库时已保证）。
func isDate(s string) bool {
	if len(s) != 10 || s[4] != '-' || s[7] != '-' {
		return false
	}
	_, err1 := strconv.Atoi(s[0:4])
	_, err2 := strconv.Atoi(s[5:7])
	_, err3 := strconv.Atoi(s[8:10])
	return err1 == nil && err2 == nil && err3 == nil
}

type badParam string

func (b badParam) Error() string { return string(b) }

func errBadParam(msg string) error { return badParam(msg) }

// isDependencyDown 判断错误是否来自依赖服务不可用（连接失败/超时）。
// 这里用字符串匹配而非具体错误类型：database/sql 的驱动错误类型不稳定，
// 且本判断只影响 HTTP 状态码（503 vs 500），不影响业务语义。
func isDependencyDown(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	for _, needle := range []string{
		"connection refused", "no such host", "connection reset",
		"i/o timeout", "bad connection", "driver: bad connection",
		"database is closed", "too many connections",
	} {
		if strings.Contains(msg, needle) {
			return true
		}
	}
	return false
}
