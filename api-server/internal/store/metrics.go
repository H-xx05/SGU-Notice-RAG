package store

import (
	"encoding/json"
	"fmt"

	"github.com/sgu-notice-rag/api-server/internal/model"
)

// unmarshalMetrics 把 eval_runs.metrics 的 JSONB 解到六项指标。
// 六项指标名冻结（docs/api.md §5）；缺字段保持 nil，前端显示"—"而不是 0。
func unmarshalMetrics(raw []byte, dst *model.EvalMetrics) error {
	if len(raw) == 0 {
		return nil
	}
	if err := json.Unmarshal(raw, dst); err != nil {
		return fmt.Errorf("unmarshal eval metrics: %w", err)
	}
	return nil
}

// unmarshalJSONList 把 JSONB 数组解到目标切片。
//
// by_year / regressions 是**可选**字段（契约允许新增可选字段）：
// 历史行可能是 NULL、'{}'，或 E 尚未补齐快照时写的空对象。
// 这些情况都不该让看板 500 —— 拿不到就交空数组，前端显示"暂无数据"。
func unmarshalJSONList[T any](raw []byte, dst *[]T) error {
	if len(raw) == 0 || string(raw) == "null" {
		return nil
	}
	if err := json.Unmarshal(raw, dst); err != nil {
		return fmt.Errorf("unmarshal json list: %w", err)
	}
	return nil
}
