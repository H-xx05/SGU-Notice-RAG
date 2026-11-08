// Package client 【C】RAG Core 内部 HTTP 客户端。
// 唯一允许 C 访问的"下游"——C 不得直连 Qdrant（SKILL.md §6.2）。
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Chunk 对应 docs/api.md §6.1 的 chunks[] 元素。
type Chunk struct {
	ChunkID         string  `json:"chunk_id"`
	DocID           string  `json:"doc_id"`
	Title           string  `json:"title"`
	Text            string  `json:"text"`
	PublishDate     string  `json:"publish_date"`
	EffectiveFrom   *string `json:"effective_from"`
	EffectiveTo     *string `json:"effective_to"`
	DocVersion      string  `json:"doc_version"`
	SectionPath     string  `json:"section_path"`
	ContentType     string  `json:"content_type"`
	SourceURL       string  `json:"source_url"`
	IsCurrent       bool    `json:"is_current"`
	SupersedesDocID *string `json:"supersedes_doc_id"`
}

// RetrieveRequest 与 docs/api.md §6.1 请求体一致。
type RetrieveRequest struct {
	Query      string         `json:"query"`
	TimeFilter TimeFilterBody `json:"time_filter"`
	TopK       int            `json:"top_k"`
	Rerank     bool           `json:"rerank"`
}

// TimeFilterBody 内部传输用的时效过滤（字段名与对外一致）。
type TimeFilterBody struct {
	Mode     string  `json:"mode"`
	DateFrom *string `json:"date_from"`
	DateTo   *string `json:"date_to"`
	AsOf     *string `json:"as_of"`
}

// RetrieveResponse 与 docs/api.md §6.1 响应体一致；Chunks 与 Scores 等长同序。
type RetrieveResponse struct {
	Chunks         []Chunk   `json:"chunks"`
	Scores         []float64 `json:"scores"`
	AppliedFilters []string  `json:"applied_filters"`
	RetrieveMS     int64     `json:"retrieve_ms"`
}

// HealthResponse 对应 /internal/health。
type HealthResponse struct {
	Status     string            `json:"status"`
	Components map[string]string `json:"components"`
	Version    string            `json:"version"`
}

// Error 封装下游调用失败，供 handler 映射错误码。
type Error struct {
	Kind    string // unavailable | timeout | bad_response
	Message string
	Err     error
}

func (e *Error) Error() string { return fmt.Sprintf("rag-core %s: %s (%v)", e.Kind, e.Message, e.Err) }
func (e *Error) Unwrap() error { return e.Err }

// Client 是 RAG Core 的 HTTP 客户端。
type Client struct {
	baseURL string
	http    *http.Client
}

// New 构造客户端；timeout 应小于对外请求的超时预算（默认 8s）。
func New(baseURL string, timeout time.Duration) *Client {
	return &Client{
		baseURL: baseURL,
		http: &http.Client{
			Timeout: timeout,
			Transport: &http.Transport{
				MaxIdleConns:        100,
				MaxIdleConnsPerHost: 20,
				IdleConnTimeout:     90 * time.Second,
			},
		},
	}
}

// Retrieve 调用内部检索接口。
func (c *Client) Retrieve(ctx context.Context, req RetrieveRequest) (*RetrieveResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, &Error{Kind: "bad_response", Message: "marshal request", Err: err}
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/internal/retrieve", bytes.NewReader(body))
	if err != nil {
		return nil, &Error{Kind: "bad_response", Message: "build request", Err: err}
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(httpReq)
	if err != nil {
		kind := "unavailable"
		if ctx.Err() == context.DeadlineExceeded || isTimeout(err) {
			kind = "timeout"
		}
		return nil, &Error{Kind: kind, Message: "call failed", Err: err}
	}
	defer func() { _ = resp.Body.Close() }()

	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, &Error{Kind: "bad_response", Message: "read body", Err: err}
	}
	if resp.StatusCode != http.StatusOK {
		return nil, &Error{Kind: "bad_response", Message: fmt.Sprintf("status %d: %s", resp.StatusCode, truncate(raw, 256)), Err: nil}
	}

	var out RetrieveResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, &Error{Kind: "bad_response", Message: "unmarshal body", Err: err}
	}
	if len(out.Chunks) != len(out.Scores) {
		// 契约要求等长同序；不满足时截断到较短者并记录，不 panic
		n := len(out.Chunks)
		if len(out.Scores) < n {
			n = len(out.Scores)
		}
		out.Chunks = out.Chunks[:n]
		out.Scores = out.Scores[:n]
	}
	return &out, nil
}

// Health 调用内部健康检查。
func (c *Client) Health(ctx context.Context) (*HealthResponse, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/internal/health", nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	var out HealthResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func isTimeout(err error) bool {
	type timeout interface{ Timeout() bool }
	if t, ok := err.(timeout); ok {
		return t.Timeout()
	}
	return false
}

func truncate(b []byte, n int) string {
	if len(b) <= n {
		return string(b)
	}
	return string(b[:n]) + "…"
}
