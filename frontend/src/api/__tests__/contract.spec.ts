/**
 * 契约漂移探测。三道闸：
 *  1. 每个响应体的键集合必须与 docs/api.md 里列出的完全一致（少一个、多一个都红）
 *  2. 枚举值必须落在契约枚举内
 *  3. 时间/日期格式必须合法
 *
 * 这是 D 组最容易踩的坑：后端加了字段前端不知道，或者前端自己编了个字段。
 * 改契约请走 SKILL.md §1，然后同步改这里的期望集合。
 */
import { describe, expect, it } from 'vitest'
import {
  mockChatAnswer,
  mockChatRefused,
  mockEvalSummary,
  mockHealthz,
  mockNotices,
  mockToken,
} from '../mock/fixtures'
import type { Citation, NoticeItem } from '../types'

const keys = (o: object): string[] => Object.keys(o).sort()

// docs/api.md §1
const TOKEN_KEYS = ['expires_at', 'token']
// docs/api.md §2 items[]
const NOTICE_ITEM_KEYS = [
  'content_type',
  'doc_id',
  'doc_version',
  'effective_from',
  'effective_to',
  'is_current',
  'publish_date',
  'source_url',
  'supersedes_doc_id',
  'title',
]
const NOTICE_PAGE_KEYS = ['items', 'page', 'page_size', 'total']
// docs/api.md §3 citations[]
const CITATION_KEYS = [
  'doc_id',
  'doc_version',
  'effective_from',
  'effective_to',
  'publish_date',
  'recency_bucket',
  'score',
  'section_path',
  'source_url',
  'title',
]
const CITATION_KEYS_WITH_SNIPPET = [...CITATION_KEYS, 'snippet'].sort()
const RETRIEVAL_KEYS = ['applied_filters', 'candidates', 'mode']
// docs/api.md §3 data
const CHAT_KEYS = [
  'answer',
  'answered_at',
  'citations',
  'latency_ms',
  'refuse_reason',
  'refused',
  'retrieval',
]
const EVAL_KEYS = ['by_year', 'created_at', 'dataset_size', 'metrics', 'regressions', 'run_id']
const METRIC_KEYS = [
  'answer_relevancy',
  'context_precision',
  'context_recall',
  'faithfulness',
  'refusal_rate',
  'stale_recall_rate',
]

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)$/

describe('§1 auth/token', () => {
  it('TokenResponse 键集合与契约一致', () => {
    expect(keys(mockToken)).toEqual(TOKEN_KEYS)
  })
})

describe('§2 notices', () => {
  it('NoticePage 键集合与契约一致', () => {
    expect(keys(mockNotices)).toEqual(NOTICE_PAGE_KEYS)
  })

  it('NoticeItem 键集合与契约一致', () => {
    for (const item of mockNotices.items) {
      expect(keys(item)).toEqual(NOTICE_ITEM_KEYS)
    }
  })

  it('日期字段格式合法（effective_to 允许 null = 长期有效）', () => {
    for (const item of mockNotices.items) {
      expect(item.publish_date).toMatch(ISO_DATE)
      expect(item.effective_from === null || ISO_DATE.test(item.effective_from)).toBe(true)
      expect(item.effective_to === null || ISO_DATE.test(item.effective_to)).toBe(true)
    }
  })

  it('存在一条长期有效（effective_to=null）的样例', () => {
    // 端不到这条，前端就会把"无截止日"渲染成"已过期"
    expect(mockNotices.items.some((n) => n.effective_to === null && n.is_current)).toBe(true)
  })

  it('存在一条被替代（is_current=false）的样例', () => {
    expect(mockNotices.items.some((n) => !n.is_current)).toBe(true)
  })

  it('content_type 取值在枚举内', () => {
    const allowed = new Set(['notice_body', 'attachment', 'table', 'appendix'])
    mockNotices.items.forEach((n: NoticeItem) => expect(allowed.has(n.content_type)).toBe(true))
  })
})

describe('§3 chat', () => {
  it('ChatResponse 键集合与契约一致', () => {
    expect(keys(mockChatAnswer)).toEqual(CHAT_KEYS)
    // 拒答样例必须同形 —— 前端不能靠"字段在不在"判断是否拒答
    expect(keys(mockChatRefused)).toEqual(CHAT_KEYS)
  })

  it('citations 键集合与契约一致（非流式含 snippet）', () => {
    for (const c of mockChatAnswer.citations) {
      expect(keys(c)).toEqual(CITATION_KEYS_WITH_SNIPPET)
    }
  })

  it('retrieval 键集合与契约一致', () => {
    expect(keys(mockChatAnswer.retrieval)).toEqual(RETRIEVAL_KEYS)
  })

  it('recency_bucket 取值在枚举内', () => {
    const allowed = new Set(['current', 'stale', 'unknown'])
    mockChatAnswer.citations.forEach((c: Citation) =>
      expect(allowed.has(c.recency_bucket)).toBe(true),
    )
  })

  it('score 在 [0,1]', () => {
    mockChatAnswer.citations.forEach((c) => {
      expect(c.score).toBeGreaterThanOrEqual(0)
      expect(c.score).toBeLessThanOrEqual(1)
    })
  })

  it('拒答样例：refused=true 且 citations 为空且 reason 在枚举内', () => {
    expect(mockChatRefused.refused).toBe(true)
    expect(mockChatRefused.citations).toHaveLength(0)
    expect(['no_hit', 'all_stale', 'low_score']).toContain(mockChatRefused.refuse_reason)
  })

  it('非拒答样例：refuse_reason 必须为 null', () => {
    expect(mockChatAnswer.refused).toBe(false)
    expect(mockChatAnswer.refuse_reason).toBeNull()
  })

  it('answered_at 是 RFC3339', () => {
    expect(mockChatAnswer.answered_at).toMatch(RFC3339)
  })
})

describe('§5 eval/summary', () => {
  it('EvalSummary 键集合与契约一致', () => {
    expect(keys(mockEvalSummary)).toEqual(EVAL_KEYS)
  })

  it('metrics 六个指标名冻结', () => {
    expect(keys(mockEvalSummary.metrics)).toEqual(METRIC_KEYS)
  })

  it('by_year 元素键集合与契约一致', () => {
    for (const y of mockEvalSummary.by_year) {
      expect(keys(y)).toEqual(['count', 'stale_recall_rate', 'year'])
    }
  })

  it('regressions 元素键集合与契约一致', () => {
    for (const r of mockEvalSummary.regressions) {
      expect(keys(r)).toEqual(['delta', 'metric', 'vs_run'])
    }
  })
})

describe('healthz', () => {
  it('status 取值在枚举内', () => {
    expect(['ok', 'degraded']).toContain(mockHealthz.status)
  })
})
