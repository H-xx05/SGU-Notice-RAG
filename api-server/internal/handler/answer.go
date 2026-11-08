package handler

import (
	"strings"

	"github.com/sgu-notice-rag/api-server/internal/client"
)

// extractiveAnswer 是默认的答案生成实现：**抽取式**，不调用任何外部 LLM。
//
// 为什么默认抽取式：
//  1. 骨架阶段必须能离线跑通（RAG_STUB_MODE=true），不依赖 API Key；
//  2. 抽取式答案天然"可溯源"——每句话都来自 citation，Faithfulness 不会因生成层丢分；
//  3. 时效性判定（本项目的核心命题）发生在 B 的检索侧，答案层越"薄"越不容易引入幻觉。
//
// 接 LLM 时不要改本函数，改 h.WithAnswerFunc(...)（见 docs/architecture.md §2 时序图）。
//
// 输出形态：先给一句"结论句"（取最高分 chunk 的首个完整句），再补一行出处摘要。
func extractiveAnswer(_ string, chunks []client.Chunk) string {
	if len(chunks) == 0 {
		return ""
	}

	top := chunks[0]
	body := firstSentence(top.Text, top.Title)
	if body == "" {
		body = truncateRunes(strings.TrimSpace(top.Text), 120)
	}
	if body == "" {
		return ""
	}

	var b strings.Builder
	b.WriteString(body)

	// 出处行：让用户不用点开引用也能看到"这条依据什么时候有效"。
	period := effectiveLine(top)
	if period != "" {
		b.WriteString("\n\n依据：")
		if top.SectionPath != "" {
			b.WriteString(top.SectionPath)
			b.WriteString("，")
		}
		if top.DocVersion != "" {
			b.WriteString(top.DocVersion)
			b.WriteString("，")
		}
		b.WriteString(period)
	}
	return b.String()
}

// effectiveLine 生成"发布于 X，有效期 Y ~ Z"的中文短句；缺失字段一律跳过，不编造。
func effectiveLine(ch client.Chunk) string {
	parts := make([]string, 0, 2)
	if ch.PublishDate != "" {
		parts = append(parts, "发布于 "+ch.PublishDate)
	}
	switch {
	case ch.EffectiveFrom != nil && ch.EffectiveTo != nil:
		parts = append(parts, "有效期 "+*ch.EffectiveFrom+" ~ "+*ch.EffectiveTo)
	case ch.EffectiveFrom != nil:
		parts = append(parts, *ch.EffectiveFrom+" 起施行")
	case ch.EffectiveTo != nil:
		parts = append(parts, "有效期至 "+*ch.EffectiveTo)
	}
	return strings.Join(parts, "，")
}

// firstSentence 取正文首个完整句（中文句号/分号/换行/英文句点皆视为句界）。
// chunk 首行通常与标题重复，这里显式跳过，避免答案退化成标题复读。
func firstSentence(text, title string) string {
	title = strings.TrimSpace(title)
	lines := strings.Split(text, "\n")
	for _, ln := range lines {
		ln = strings.TrimSpace(ln)
		if ln == "" {
			continue
		}
		// 与标题同名的行（或标题的片段行）不是正文，跳过。
		if title != "" && (ln == title || (len([]rune(ln)) >= 8 && strings.Contains(title, ln))) {
			continue
		}
		if idx := sentenceEnd(ln); idx > 0 {
			return strings.TrimSpace(ln[:idx])
		}
		if len([]rune(ln)) >= 12 { // 无标点的长行直接当作一句
			return truncateRunes(ln, 120)
		}
	}
	return ""
}

func sentenceEnd(s string) int {
	for i, r := range s {
		switch r {
		case '。', '；', '！', '？', '.', '!', '?':
			return i + lenRune(r)
		case '\n':
			return i
		}
	}
	return -1
}

func lenRune(r rune) int {
	switch {
	case r < 0x80:
		return 1
	case r < 0x800:
		return 2
	case r < 0x10000:
		return 3
	default:
		return 4
	}
}

// truncateRunes 按 rune 截断，避免切坏中文。
func truncateRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…"
}
