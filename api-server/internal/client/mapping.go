package client

import (
	"github.com/sgu-notice-rag/api-server/internal/model"
)

// ToRetrieveRequest 把对外 ChatRequest 翻译成内部检索请求。
// 默认值收敛点：top_k 缺省 8、time_filter.mode 缺省 auto（见 docs/api.md §3）。
func ToRetrieveRequest(req model.ChatRequest) RetrieveRequest {
	topK := req.TopK
	if topK <= 0 {
		topK = 8
	}
	if topK > 20 {
		topK = 20
	}
	mode := req.TimeFilter.Mode
	if mode == "" {
		mode = "auto"
	}
	return RetrieveRequest{
		Query: req.Query,
		TimeFilter: TimeFilterBody{
			Mode:     mode,
			DateFrom: req.TimeFilter.DateFrom,
			DateTo:   req.TimeFilter.DateTo,
			AsOf:     req.TimeFilter.AsOf,
		},
		TopK:   topK,
		Rerank: true,
	}
}

// ToCitations 把检索 chunk + 分数映射为对外 Citation。
// recency_bucket 由 is_current + 有效期推导（前端只展示，不做判定）。
func ToCitations(chunks []Chunk, scores []float64, asOf string) []model.Citation {
	out := make([]model.Citation, 0, len(chunks))
	for i, ch := range chunks {
		score := 0.0
		if i < len(scores) {
			score = scores[i]
		}
		out = append(out, model.Citation{
			DocID:         ch.DocID,
			Title:         ch.Title,
			PublishDate:   ch.PublishDate,
			EffectiveFrom: ch.EffectiveFrom,
			EffectiveTo:   ch.EffectiveTo,
			DocVersion:    ch.DocVersion,
			SectionPath:   ch.SectionPath,
			SourceURL:     ch.SourceURL,
			Snippet:       snippet(ch.Text, 120),
			Score:         score,
			RecencyBucket: recencyBucket(ch, asOf),
		})
	}
	return out
}

// recencyBucket 判定引用新鲜度：
//   - is_current=false                       → stale
//   - effective_to 早于 asOf                 → stale
//   - 其余且有 publish_date                  → current
//   - publish_date 为空                      → unknown
func recencyBucket(ch Chunk, asOf string) string {
	if ch.PublishDate == "" {
		return "unknown"
	}
	if !ch.IsCurrent {
		return "stale"
	}
	if ch.EffectiveTo != nil && *ch.EffectiveTo != "" && *ch.EffectiveTo < asOf {
		return "stale"
	}
	return "current"
}

// snippet 截取前 n 个字符（按 rune 计，避免切坏中文）。
func snippet(text string, n int) string {
	r := []rune(text)
	if len(r) <= n {
		return text
	}
	return string(r[:n]) + "…"
}
