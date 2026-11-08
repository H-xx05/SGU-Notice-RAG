// Package model 【C】对外 REST 的数据结构。
// 字段名与 docs/api.md 逐字一致；JSON tag 变更 = 契约变更（SKILL.md §1）。
package model

import "encoding/json"

// ---------- 统一响应包 ----------

// Envelope 是所有非 SSE 响应的外层包装：{code, message, data}
type Envelope struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

// ---------- notices ----------

// NoticeItem 对应 docs/api.md §2 的 items[] 元素。
type NoticeItem struct {
	DocID           string  `json:"doc_id"`
	Title           string  `json:"title"`
	PublishDate     string  `json:"publish_date"`
	EffectiveFrom   *string `json:"effective_from"`
	EffectiveTo     *string `json:"effective_to"`
	DocVersion      string  `json:"doc_version"`
	IsCurrent       bool    `json:"is_current"`
	SupersedesDocID *string `json:"supersedes_doc_id"`
	ContentType     string  `json:"content_type"`
	SourceURL       string  `json:"source_url"`
}

// NoticePage 对应 docs/api.md §2 的 data。
type NoticePage struct {
	Items    []NoticeItem `json:"items"`
	Total    int          `json:"total"`
	Page     int          `json:"page"`
	PageSize int          `json:"page_size"`
}

// ---------- chat ----------

// TimeFilter 时效过滤参数。
type TimeFilter struct {
	Mode     string  `json:"mode"`      // auto | off | range
	DateFrom *string `json:"date_from"` // YYYY-MM-DD
	DateTo   *string `json:"date_to"`
	AsOf     *string `json:"as_of"`
}

// ChatRequest 对应 docs/api.md §3 请求体。
type ChatRequest struct {
	Query      string     `json:"query" binding:"required,min=1,max=512"`
	SessionID  *string    `json:"session_id"`
	TopK       int        `json:"top_k"`
	TimeFilter TimeFilter `json:"time_filter"`
}

// Citation 答案引用。
type Citation struct {
	DocID         string  `json:"doc_id"`
	Title         string  `json:"title"`
	PublishDate   string  `json:"publish_date"`
	EffectiveFrom *string `json:"effective_from"`
	EffectiveTo   *string `json:"effective_to"`
	DocVersion    string  `json:"doc_version"`
	SectionPath   string  `json:"section_path"`
	SourceURL     string  `json:"source_url"`
	Snippet       string  `json:"snippet"`
	Score         float64 `json:"score"`
	RecencyBucket string  `json:"recency_bucket"` // current | stale | unknown
}

// RetrievalMeta 检索元信息，用于前端展示"是否做了时间过滤"。
type RetrievalMeta struct {
	Mode           string   `json:"mode"`
	AppliedFilters []string `json:"applied_filters"`
	Candidates     int      `json:"candidates"`
}

// ChatResponse 对应 docs/api.md §3 data。
type ChatResponse struct {
	Answer       string        `json:"answer"`
	Refused      bool          `json:"refused"`
	RefuseReason *string       `json:"refuse_reason"`
	Citations    []Citation    `json:"citations"`
	Retrieval    RetrievalMeta `json:"retrieval"`
	LatencyMS    int64         `json:"latency_ms"`
	AnsweredAt   string        `json:"answered_at"`
}

// ---------- eval ----------

// EvalMetrics 六项指标，名称冻结。
type EvalMetrics struct {
	Faithfulness     *float64 `json:"faithfulness"`
	AnswerRelevancy  *float64 `json:"answer_relevancy"`
	ContextPrecision *float64 `json:"context_precision"`
	ContextRecall    *float64 `json:"context_recall"`
	StaleRecallRate  *float64 `json:"stale_recall_rate"`
	RefusalRate      *float64 `json:"refusal_rate"`
}

// EvalByYear 分年度指标。
type EvalByYear struct {
	Year            int     `json:"year"`
	StaleRecallRate float64 `json:"stale_recall_rate"`
	Count           int     `json:"count"`
}

// EvalRegression 与上一次跑批的对比。
type EvalRegression struct {
	Metric string  `json:"metric"`
	Delta  float64 `json:"delta"`
	VsRun  string  `json:"vs_run"`
}

// EvalSummary 对应 docs/api.md §5 data。
type EvalSummary struct {
	RunID       *string          `json:"run_id"`
	CreatedAt   *string          `json:"created_at"`
	DatasetSize int              `json:"dataset_size"`
	Metrics     EvalMetrics      `json:"metrics"`
	ByYear      []EvalByYear     `json:"by_year"`
	Regressions []EvalRegression `json:"regressions"`
}

// ---------- auth ----------

// TokenRequest 仅开发环境可用的取 token 接口。
type TokenRequest struct {
	UserID string `json:"user_id" binding:"required"`
	Role   string `json:"role"`
}

// TokenResponse 对应 docs/api.md §1 data。
type TokenResponse struct {
	Token     string `json:"token"`
	ExpiresAt string `json:"expires_at"`
}
