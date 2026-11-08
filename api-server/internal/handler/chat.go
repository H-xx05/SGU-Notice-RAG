package handler

import (
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/sgu-notice-rag/api-server/internal/client"
	"github.com/sgu-notice-rag/api-server/internal/model"
	"github.com/sgu-notice-rag/api-server/internal/response"
)

// ---------- 内部复用：检索 + 拒答判定 ----------

// retrievalResult 是一次问答的中间结果，非流式与 SSE 共用，避免两条路径判定不一致。
type retrievalResult struct {
	retrieve  *client.RetrieveResponse
	citations []model.Citation
	answer    string
	refused   bool
	refuseWhy *string
	meta      model.RetrievalMeta
}

// runRetrieval 执行「调 B → 判拒答 → 生成答案」。
// 拒答三态与 docs/api.md §3 一致：no_hit / all_stale / low_score，顺序即优先级。
func (h *Handler) runRetrieval(c *gin.Context, req model.ChatRequest) (*retrievalResult, int, int) {
	asOf := h.today()
	if req.TimeFilter.AsOf != nil && *req.TimeFilter.AsOf != "" {
		asOf = *req.TimeFilter.AsOf
	}

	upstreamReq := client.ToRetrieveRequest(req)
	if upstreamReq.TimeFilter.AsOf == nil {
		upstreamReq.TimeFilter.AsOf = strPtr(asOf)
	}

	resp, err := h.rag.Retrieve(c.Request.Context(), upstreamReq)
	if err != nil {
		if ce, ok := err.(*client.Error); ok && ce.Kind == "timeout" {
			return nil, http.StatusGatewayTimeout, response.CodeRAGTimeout
		}
		return nil, http.StatusBadGateway, response.CodeRAGUnavailable
	}

	res := &retrievalResult{retrieve: resp}
	res.citations = client.ToCitations(resp.Chunks, resp.Scores, asOf)

	mode := req.TimeFilter.Mode
	if mode == "" {
		mode = "auto"
	}
	res.meta = model.RetrievalMeta{
		Mode:           mode,
		AppliedFilters: nonNilStrings(resp.AppliedFilters),
		Candidates:     len(resp.Chunks),
	}

	switch {
	case len(resp.Chunks) == 0:
		res.refused, res.refuseWhy = true, strPtr(RefuseNoHit)
	case allStale(res.citations):
		res.refused, res.refuseWhy = true, strPtr(RefuseAllStale)
	case len(resp.Scores) > 0 && resp.Scores[0] < MinAnswerScore:
		res.refused, res.refuseWhy = true, strPtr(RefuseLowScore)
	default:
		res.answer = h.answerFn(req.Query, resp.Chunks)
		if strings.TrimSpace(res.answer) == "" {
			// 生成失败 → 降级为拒答，绝不返回空串（前端会白屏）
			res.refused, res.refuseWhy = true, strPtr(RefuseLowScore)
		}
	}
	if res.refused {
		res.answer = RefusalAnswer
	}

	return res, http.StatusOK, response.CodeOK
}

// allStale 判断召回是否全部过期；空集合不算（由 no_hit 负责）。
func allStale(cits []model.Citation) bool {
	if len(cits) == 0 {
		return false
	}
	for _, c := range cits {
		if c.RecencyBucket != "stale" {
			return false
		}
	}
	return true
}

// nonNilStrings 保证 JSON 输出 [] 而不是 null（契约示例是数组）。
func nonNilStrings(in []string) []string {
	if in == nil {
		return []string{}
	}
	return in
}

// ---------- POST /api/v1/chat ----------

// Chat 处理非流式问答（docs/api.md §3）。
func (h *Handler) Chat(c *gin.Context) {
	var req model.ChatRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.Fail(c, http.StatusBadRequest, response.CodeBadRequest, "query 不能为空，且长度不超过 512")
		return
	}
	req.Query = strings.TrimSpace(req.Query)
	if req.Query == "" {
		response.Fail(c, http.StatusBadRequest, response.CodeBadRequest, "query 不能为空")
		return
	}
	if reason := validateTimeFilter(req.TimeFilter); reason != "" {
		response.Fail(c, http.StatusBadRequest, response.CodeBadRequest, reason)
		return
	}

	res, status, code := h.runRetrieval(c, req)
	if res == nil {
		response.Fail(c, status, code, "")
		return
	}

	response.OK(c, model.ChatResponse{
		Answer:       res.answer,
		Refused:      res.refused,
		RefuseReason: res.refuseWhy,
		Citations:    res.citations,
		Retrieval:    res.meta,
		LatencyMS:    res.retrieve.RetrieveMS,
		AnsweredAt:   h.nowRFC3339(),
	})
}

// validateTimeFilter 校验 time_filter 组合（docs/api.md §3 约束表）。
func validateTimeFilter(tf model.TimeFilter) string {
	switch tf.Mode {
	case "", "auto", "off":
		return ""
	case "range":
		if tf.DateFrom == nil || tf.DateTo == nil || *tf.DateFrom == "" || *tf.DateTo == "" {
			return "mode=range 时 date_from 与 date_to 必填"
		}
		if !isDate(*tf.DateFrom) || !isDate(*tf.DateTo) {
			return "date_from / date_to 必须是 YYYY-MM-DD"
		}
		if *tf.DateFrom > *tf.DateTo {
			return "date_from 不能晚于 date_to"
		}
		return ""
	default:
		return "time_filter.mode 只能是 auto|off|range"
	}
}

// ---------- GET /api/v1/chat/stream（SSE） ----------

// streamWriter 串行化 SSE 写入（keepalive 协程与主协程会并发写）。
type streamWriter struct {
	mu     sync.Mutex
	c      *gin.Context
	closed bool
}

func newStreamWriter(c *gin.Context) *streamWriter {
	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("Connection", "keep-alive")
	c.Header("X-Accel-Buffering", "no") // Nginx 不缓冲（docs/api.md §4）
	c.Status(http.StatusOK)
	return &streamWriter{c: c}
}

func (s *streamWriter) event(name, payload string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return
	}
	_, _ = s.c.Writer.WriteString("event: " + name + "\ndata: " + payload + "\n\n")
	s.c.Writer.Flush()
}

func (s *streamWriter) comment(text string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return
	}
	_, _ = s.c.Writer.WriteString(": " + text + "\n\n")
	s.c.Writer.Flush()
}

func (s *streamWriter) close() {
	s.mu.Lock()
	s.closed = true
	s.mu.Unlock()
}

// ChatStream 处理 SSE 问答（docs/api.md §4）。
// 事件序固定：meta → sources → delta×N → done；出错发 error 后关闭。
func (h *Handler) ChatStream(c *gin.Context) {
	query := strings.TrimSpace(c.Query("q"))
	if query == "" {
		response.Fail(c, http.StatusBadRequest, response.CodeBadRequest, "q 不能为空")
		return
	}

	topK := intParam(c, "top_k", 8)
	if topK < 1 || topK > 20 {
		response.Fail(c, http.StatusBadRequest, response.CodeBadRequest, "top_k 必须在 1–20 之间")
		return
	}

	req := model.ChatRequest{
		Query:      query,
		TopK:       topK,
		TimeFilter: model.TimeFilter{Mode: "auto"},
	}

	sw := newStreamWriter(c)
	defer sw.close()

	// keepalive：每 10s 一行注释（契约要求 ≤15s），防 Nginx proxy_read_timeout。
	stop := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-stop:
				return
			case <-ticker.C:
				sw.comment("keepalive")
			case <-c.Request.Context().Done():
				return
			}
		}
	}()
	defer func() {
		close(stop)
		wg.Wait()
	}()

	res, _, code := h.runRetrieval(c, req)
	if res == nil {
		sw.event("error", mustJSON(map[string]any{
			"code":    code,
			"message": response.Message[code],
		}))
		return
	}

	// 1) meta
	sw.event("meta", mustJSON(map[string]any{
		"session_id": "",
		"retrieval":  res.meta,
	}))

	// 2) sources
	sw.event("sources", mustJSON(map[string]any{"citations": res.citations}))

	// 3) delta×N —— 至少 1 个（契约要求）
	for _, piece := range splitForStream(res.answer) {
		select {
		case <-c.Request.Context().Done():
			return // 客户端断开：上游 HTTP 随之取消（context 传递，契约 ≤2s）
		default:
		}
		sw.event("delta", mustJSON(map[string]any{"text": piece}))
	}

	// 4) done
	sw.event("done", mustJSON(map[string]any{
		"latency_ms": res.retrieve.RetrieveMS,
		"refused":    res.refused,
	}))
}

// splitForStream 把整段答案按中文句界切片。
// 抽取式答案一次成型，不按 token 生成；按句切片既满足"N≥1"，又让前端有渐进效果。
func splitForStream(answer string) []string {
	if strings.TrimSpace(answer) == "" {
		return []string{""}
	}
	var out []string
	var cur strings.Builder
	for _, r := range answer {
		cur.WriteRune(r)
		if r == '。' || r == '；' || r == '！' || r == '？' || r == '\n' {
			out = append(out, cur.String())
			cur.Reset()
		}
	}
	if cur.Len() > 0 {
		out = append(out, cur.String())
	}
	if len(out) == 0 {
		out = []string{answer}
	}
	return out
}
