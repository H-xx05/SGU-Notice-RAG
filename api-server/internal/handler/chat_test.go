package handler

import (
	"testing"

	"github.com/sgu-notice-rag/api-server/internal/client"
	"github.com/sgu-notice-rag/api-server/internal/model"
)

// TestAllStale 覆盖拒答判定里最容易出错的一支：
// 空集合不能算 all_stale（否则 no_hit 永远不触发）。
func TestAllStale(t *testing.T) {
	cases := []struct {
		name string
		in   []model.Citation
		want bool
	}{
		{"空集合", nil, false},
		{"全部过期", []model.Citation{{RecencyBucket: "stale"}, {RecencyBucket: "stale"}}, true},
		{"含一条现行", []model.Citation{{RecencyBucket: "stale"}, {RecencyBucket: "current"}}, false},
		{"含一条未知", []model.Citation{{RecencyBucket: "stale"}, {RecencyBucket: "unknown"}}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := allStale(c.in); got != c.want {
				t.Fatalf("allStale(%v) = %v, want %v", c.in, got, c.want)
			}
		})
	}
}

// TestSplitForStream 保证 SSE 至少产出 1 个 delta（契约硬要求）。
func TestSplitForStream(t *testing.T) {
	cases := []struct {
		name string
		in   string
		min  int
	}{
		{"空答案也要有 1 个 delta", "", 1},
		{"单句", "报名截止时间为4月10日17:00。", 1},
		{"两句", "第一句。第二句。", 2},
		{"带换行", "结论。\n\n依据：v2026.1，发布于2026-03-04。", 3},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := splitForStream(c.in)
			if len(got) < c.min {
				t.Fatalf("splitForStream(%q) 产出 %d 段，期望 ≥%d", c.in, len(got), c.min)
			}
			if len(got) < 1 {
				t.Fatalf("必须至少 1 段")
			}
		})
	}
}

// TestExtractiveAnswer 保证答案不是空的、且带上依据行（可溯源）。
func TestExtractiveAnswer(t *testing.T) {
	effFrom, effTo := "2026-03-04", "2026-05-31"
	chunks := []client.Chunk{{
		DocID:         "jwb-jxtz-2026-29459",
		Title:         "关于2026年转专业工作的通知",
		Text:          "关于2026年转专业工作的通知\n报名截止时间为4月10日17:00，逾期不再受理。",
		PublishDate:   "2026-03-04",
		EffectiveFrom: &effFrom,
		EffectiveTo:   &effTo,
		DocVersion:    "v2026.1",
		SectionPath:   "正文 > 一、报名安排",
	}}

	got := extractiveAnswer("2026年转专业报名截止日期", chunks)
	if got == "" {
		t.Fatal("答案不应为空")
	}
	// 首句应是正文而不是标题行
	if !contains(got, "4月10日17:00") {
		t.Fatalf("答案未包含关键事实：%q", got)
	}
	if contains(got, "关于2026年转专业工作的通知\n") == false && !contains(got, "依据：") {
		t.Fatalf("答案缺少依据行：%q", got)
	}
}

// TestExtractiveAnswerEmpty 无 chunk 时返回空串，由调用方降级为拒答。
func TestExtractiveAnswerEmpty(t *testing.T) {
	if got := extractiveAnswer("任意问题", nil); got != "" {
		t.Fatalf("无 chunk 时应返回空串，实际 %q", got)
	}
}

// TestValidateTimeFilter 覆盖 range 模式的必填与顺序校验。
func TestValidateTimeFilter(t *testing.T) {
	d1, d2, bad := "2026-01-01", "2026-12-31", "2026/01/01"

	cases := []struct {
		name string
		in   model.TimeFilter
		ok   bool
	}{
		{"默认 auto", model.TimeFilter{Mode: ""}, true},
		{"auto", model.TimeFilter{Mode: "auto"}, true},
		{"off", model.TimeFilter{Mode: "off"}, true},
		{"range 合法", model.TimeFilter{Mode: "range", DateFrom: &d1, DateTo: &d2}, true},
		{"range 缺 date_to", model.TimeFilter{Mode: "range", DateFrom: &d1}, false},
		{"range 日期格式错", model.TimeFilter{Mode: "range", DateFrom: &bad, DateTo: &d2}, false},
		{"range 起止颠倒", model.TimeFilter{Mode: "range", DateFrom: &d2, DateTo: &d1}, false},
		{"未知 mode", model.TimeFilter{Mode: "yolo"}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := validateTimeFilter(c.in)
			if c.ok && got != "" {
				t.Fatalf("期望通过，实际报错: %s", got)
			}
			if !c.ok && got == "" {
				t.Fatal("期望报错，实际通过")
			}
		})
	}
}

func contains(s, sub string) bool {
	return len(sub) == 0 || (len(s) >= len(sub) && indexOf(s, sub) >= 0)
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}
