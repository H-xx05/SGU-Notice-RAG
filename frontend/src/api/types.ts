/**
 * 与 docs/api.md 逐字对应的类型。改动顺序：
 *   1. 先改 docs/api.md（走 SKILL.md §1 契约流程）
 *   2. 再改本文件 + mock/fixtures.ts
 *   3. 最后跑 `pnpm test`，contract.spec.ts 用键集合逐字比对拦住漂移
 */

export interface Envelope<T> {
  code: number
  message: string
  data: T
}

/** 业务码；0 成功，其余见 docs/api.md §7 */
export const CODE_OK = 0
export const CODE_BAD_PARAM = 40001
export const CODE_UNAUTHORIZED = 40101
export const CODE_FORBIDDEN = 40301
export const CODE_NOT_FOUND = 40401
export const CODE_RATE_LIMITED = 42901
export const CODE_RAG_UNAVAILABLE = 50201
export const CODE_RAG_TIMEOUT = 50202
export const CODE_DEPENDENCY_DOWN = 50301
export const CODE_INTERNAL = 50001

// ---------- §1 auth ----------

export interface TokenRequest {
  user_id: string
  role: 'student' | 'admin'
}

export interface TokenResponse {
  token: string
  expires_at: string
}

// ---------- §2 notices ----------

export type ContentType = 'notice_body' | 'attachment' | 'table' | 'appendix'

export interface NoticeItem {
  doc_id: string
  title: string
  publish_date: string
  effective_from: string | null
  effective_to: string | null
  doc_version: string
  is_current: boolean
  supersedes_doc_id: string | null
  content_type: ContentType
  source_url: string
}

export interface NoticePage {
  items: NoticeItem[]
  total: number
  page: number
  page_size: number
}

export interface NoticeQuery {
  page?: number
  page_size?: number
  year?: number
  q?: string
  publish_from?: string
  publish_to?: string
  content_type?: ContentType
  doc_version?: string
  /** false 时只返回 is_current=true */
  include_stale?: boolean
}

// ---------- §3 chat ----------

export type TimeMode = 'auto' | 'off' | 'range'

export interface TimeFilter {
  mode: TimeMode
  date_from: string | null
  date_to: string | null
  /** null → 服务端取今天 */
  as_of: string | null
}

export interface ChatRequest {
  query: string
  session_id?: string | null
  top_k?: number
  time_filter?: Partial<TimeFilter>
}

export type RecencyBucket = 'current' | 'stale' | 'unknown'
export type RefuseReason = 'no_hit' | 'all_stale' | 'low_score'

export interface Citation {
  doc_id: string
  title: string
  publish_date: string
  effective_from: string | null
  effective_to: string | null
  doc_version: string
  section_path: string
  source_url: string
  /** 流式响应里没有 snippet */
  snippet?: string
  score: number
  recency_bucket: RecencyBucket
}

export interface RetrievalMeta {
  mode: TimeMode
  applied_filters: string[]
  candidates: number
}

export interface ChatResponse {
  answer: string
  refused: boolean
  refuse_reason: RefuseReason | null
  citations: Citation[]
  retrieval: RetrievalMeta
  latency_ms: number
  answered_at: string
}

// ---------- §4 SSE ----------

export interface StreamMetaEvent {
  session_id: string
  retrieval: RetrievalMeta
}

export interface StreamSourcesEvent {
  citations: Citation[]
}

export interface StreamDeltaEvent {
  text: string
}

export interface StreamDoneEvent {
  latency_ms: number
  refused: boolean
  refuse_reason?: RefuseReason | null
}

export interface StreamErrorEvent {
  code: number
  message: string
}

// ---------- §5 eval ----------

export interface EvalMetrics {
  faithfulness: number | null
  answer_relevancy: number | null
  context_precision: number | null
  context_recall: number | null
  stale_recall_rate: number | null
  refusal_rate: number | null
}

export interface EvalByYear {
  year: number
  stale_recall_rate: number | null
  count: number
}

export interface EvalRegression {
  metric: string
  delta: number
  vs_run: string
}

export interface EvalSummary {
  run_id: string | null
  created_at: string | null
  dataset_size: number
  metrics: EvalMetrics
  by_year: EvalByYear[]
  regressions: EvalRegression[]
}

// ---------- 健康检查 ----------

export interface Healthz {
  status: 'ok' | 'degraded'
  version: string
  components?: Record<string, string>
}
