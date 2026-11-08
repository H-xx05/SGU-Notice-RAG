// Package store 【C】Postgres 访问层（notices / eval）。
// 预留：只暴露 Handler 需要的两个查询，其余待 C 补实现。
package store

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
	"time"

	_ "github.com/lib/pq"

	"github.com/sgu-notice-rag/api-server/internal/model"
)

// Store 封装数据库句柄。
type Store struct {
	db *sql.DB
}

// Open 建立连接池；调用方负责 Close。
func Open(dsn string) (*Store, error) {
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(25)
	db.SetMaxIdleConns(10)
	db.SetConnMaxLifetime(30 * time.Minute)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("ping postgres: %w", err)
	}
	return &Store{db: db}, nil
}

// Close 关闭连接池。
func (s *Store) Close() error { return s.db.Close() }

// Ping 健康检查。
func (s *Store) Ping(ctx context.Context) error { return s.db.PingContext(ctx) }

// NoticeFilter 是 GET /api/v1/notices 的查询条件。
type NoticeFilter struct {
	Page         int
	PageSize     int
	Year         int
	Q            string
	PublishFrom  string
	PublishTo    string
	ContentType  string
	DocVersion   string
	IncludeStale bool
}

// ListNotices 分页查询通知。TODO(C): 用 sqlx 或 squirrel 重写为更可读的查询构造。
func (s *Store) ListNotices(ctx context.Context, f NoticeFilter) (*model.NoticePage, error) {
	if f.Page <= 0 {
		f.Page = 1
	}
	if f.PageSize <= 0 {
		f.PageSize = 20
	}
	if f.PageSize > 100 {
		f.PageSize = 100
	}

	where := []string{"1=1"}
	args := []any{}
	idx := 1

	add := func(clause string, val any) {
		where = append(where, fmt.Sprintf(clause, idx))
		args = append(args, val)
		idx++
	}

	if !f.IncludeStale {
		where = append(where, "is_current = TRUE")
	}
	if f.Year > 0 {
		add("EXTRACT(YEAR FROM publish_date) = $%d", f.Year)
	}
	if f.Q != "" {
		add("title ILIKE '%%' || $%d || '%%'", f.Q)
	}
	if f.PublishFrom != "" {
		add("publish_date >= $%d", f.PublishFrom)
	}
	if f.PublishTo != "" {
		add("publish_date <= $%d", f.PublishTo)
	}
	if f.ContentType != "" {
		add("content_type = $%d", f.ContentType)
	}
	if f.DocVersion != "" {
		add("doc_version = $%d", f.DocVersion)
	}

	whereSQL := strings.Join(where, " AND ")

	var total int
	countSQL := "SELECT COUNT(*) FROM notices WHERE " + whereSQL
	if err := s.db.QueryRowContext(ctx, countSQL, args...).Scan(&total); err != nil {
		return nil, fmt.Errorf("count notices: %w", err)
	}

	offset := (f.Page - 1) * f.PageSize
	querySQL := fmt.Sprintf(`
		SELECT doc_id, title, to_char(publish_date,'YYYY-MM-DD'),
		       to_char(effective_from,'YYYY-MM-DD'), to_char(effective_to,'YYYY-MM-DD'),
		       doc_version, is_current, supersedes_doc_id, content_type, source_url
		FROM notices
		WHERE %s
		ORDER BY publish_date DESC, doc_id DESC
		LIMIT $%d OFFSET $%d`, whereSQL, idx, idx+1)

	args = append(args, f.PageSize, offset)
	rows, err := s.db.QueryContext(ctx, querySQL, args...)
	if err != nil {
		return nil, fmt.Errorf("list notices: %w", err)
	}
	defer func() { _ = rows.Close() }()

	items := make([]model.NoticeItem, 0, f.PageSize)
	for rows.Next() {
		var (
			it             model.NoticeItem
			effFrom, effTo sql.NullString
			supersedes     sql.NullString
		)
		if err := rows.Scan(&it.DocID, &it.Title, &it.PublishDate, &effFrom, &effTo,
			&it.DocVersion, &it.IsCurrent, &supersedes, &it.ContentType, &it.SourceURL); err != nil {
			return nil, fmt.Errorf("scan notice: %w", err)
		}
		if effFrom.Valid {
			v := effFrom.String
			it.EffectiveFrom = &v
		}
		if effTo.Valid {
			v := effTo.String
			it.EffectiveTo = &v
		}
		if supersedes.Valid {
			v := supersedes.String
			it.SupersedesDocID = &v
		}
		items = append(items, it)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate notices: %w", err)
	}

	return &model.NoticePage{Items: items, Total: total, Page: f.Page, PageSize: f.PageSize}, nil
}

// LatestEvalSummary 读取最近一次（或指定）评估批次。
//
// by_year / regressions 读的是 E 跑批时写下的**快照**，不是现算的聚合。
// 理由：报告一旦发出就是历史事实，事后改口径或补数据会让"当时跑出 0.08"变成
// "现在算出来 0.12"，评估看板就失去可对比性了。口径变化要用新 run_id 重新跑批。
func (s *Store) LatestEvalSummary(ctx context.Context, runID string) (*model.EvalSummary, error) {
	query := `SELECT run_id, to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SSOF'), dataset_size,
	                 metrics, by_year, regressions
	          FROM eval_runs ORDER BY created_at DESC LIMIT 1`
	args := []any{}
	if runID != "" {
		query = `SELECT run_id, to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SSOF'), dataset_size,
		                metrics, by_year, regressions
		         FROM eval_runs WHERE run_id = $1 LIMIT 1`
		args = append(args, runID)
	}

	var (
		id         string
		createdAt  string
		size       int
		metrics    []byte
		byYearRaw  []byte
		regressRaw []byte
	)
	err := s.db.QueryRowContext(ctx, query, args...).
		Scan(&id, &createdAt, &size, &metrics, &byYearRaw, &regressRaw)
	if err == sql.ErrNoRows {
		// 契约要求：无数据时 run_id=null、dataset_size=0、六项为 null
		return &model.EvalSummary{
			DatasetSize: 0,
			ByYear:      []model.EvalByYear{},
			Regressions: []model.EvalRegression{},
		}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("query eval_runs: %w", err)
	}

	out := &model.EvalSummary{
		RunID:       &id,
		CreatedAt:   &createdAt,
		DatasetSize: size,
		ByYear:      []model.EvalByYear{},
		Regressions: []model.EvalRegression{},
	}
	if err := unmarshalMetrics(metrics, &out.Metrics); err != nil {
		return nil, err
	}
	// by_year / regressions 是可选字段：历史数据里可能是 NULL 或 '{}'，
	// 解析失败不该让整个看板 500——降级为空数组，前端显示的只是"这一年没有数据"。
	if err := unmarshalJSONList(byYearRaw, &out.ByYear); err != nil {
		out.ByYear = []model.EvalByYear{}
	}
	if err := unmarshalJSONList(regressRaw, &out.Regressions); err != nil {
		out.Regressions = []model.EvalRegression{}
	}
	return out, nil
}
