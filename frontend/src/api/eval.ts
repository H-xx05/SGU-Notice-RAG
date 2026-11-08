import { request } from './client'
import { mockEvalSummary } from './mock/fixtures'
import { useMock } from './mode'
import type { EvalSummary } from './types'

/** §5 评估看板。无数据时 data 的每个字段都是空值，不是 404。 */
export async function fetchEvalSummary(runId?: string): Promise<EvalSummary> {
  if (useMock()) return mockEvalSummary
  return request<EvalSummary>('/v1/eval/summary', { query: { run_id: runId } })
}

export const emptyEvalSummary: EvalSummary = {
  run_id: null,
  created_at: null,
  dataset_size: 0,
  metrics: {
    faithfulness: null,
    answer_relevancy: null,
    context_precision: null,
    context_recall: null,
    stale_recall_rate: null,
    refusal_rate: null,
  },
  by_year: [],
  regressions: [],
}
