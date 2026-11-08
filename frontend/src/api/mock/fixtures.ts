/**
 * Mock 数据。字段名必须与 docs/api.md 的响应体完全一致 ——
 * contract.spec.ts 会拿 types.ts 的键集合去比对，缺一个就红。
 *
 * 这里刻意放了两个"陷阱"数据，让前端在 Mock 阶段就能看见真实世界的形状：
 *  1. 一条 effective_to 为 null 的长期有效规定（不能被渲染成"已过期"）
 *  2. 一条 is_current=false 的旧版本通知（必须显示"已被替代"角标）
 */
import type {
  ChatResponse,
  Citation,
  EvalSummary,
  Healthz,
  NoticePage,
  TokenResponse,
} from '../types'

export const mockToken: TokenResponse = {
  token: 'mock.jwt.token',
  expires_at: '2026-11-09T14:00:00+08:00',
}

const citations: Citation[] = [
  {
    doc_id: 'jwb-jxtz-2026-29459',
    title: '关于做好2026年转专业工作的通知',
    publish_date: '2026-03-01',
    effective_from: '2026-03-04',
    effective_to: '2026-05-31',
    doc_version: 'v2026.1',
    section_path: '正文 > 一、申请条件',
    source_url: 'https://www.sgu.edu.cn/jwb/info/1050/29459.htm',
    snippet: '全日制本科一年级、二年级学生，已修课程平均学分绩点不低于2.5的，可以申请转专业。',
    score: 0.8312,
    recency_bucket: 'current',
  },
  {
    doc_id: 'jwb-jxtz-2025-29001',
    title: '关于做好2025年转专业工作的通知',
    publish_date: '2025-03-02',
    effective_from: '2025-03-05',
    effective_to: '2025-05-30',
    doc_version: 'v2025.1',
    section_path: '正文 > 一、申请条件',
    source_url: 'https://www.sgu.edu.cn/jwb/info/1050/29001.htm',
    snippet: '已修课程平均学分绩点不低于2.0的，可以申请转专业。',
    score: 0.7411,
    recency_bucket: 'stale',
  },
  {
    doc_id: 'jwb-jxtz-2024-27810',
    title: '韶关学院学生学籍管理规定',
    publish_date: '2024-09-01',
    effective_from: '2024-09-01',
    effective_to: null,
    doc_version: 'v2024.1',
    section_path: '正文 > 第三章 学籍异动',
    source_url: 'https://www.sgu.edu.cn/jwb/info/1050/27810.htm',
    snippet: '学生转专业后，原专业已修课程的成绩与学分按学校有关规定予以认定。',
    score: 0.6902,
    recency_bucket: 'current',
  },
]

export const mockNotices: NoticePage = {
  items: [
    {
      doc_id: 'jwb-jxtz-2026-29459',
      title: '关于做好2026年转专业工作的通知',
      publish_date: '2026-03-01',
      effective_from: '2026-03-04',
      effective_to: '2026-05-31',
      doc_version: 'v2026.1',
      is_current: true,
      supersedes_doc_id: 'jwb-jxtz-2025-29001',
      content_type: 'notice_body',
      source_url: 'https://www.sgu.edu.cn/jwb/info/1050/29459.htm',
    },
    {
      doc_id: 'jwb-jxtz-2025-29001',
      title: '关于做好2025年转专业工作的通知',
      publish_date: '2025-03-02',
      effective_from: '2025-03-05',
      effective_to: '2025-05-30',
      doc_version: 'v2025.1',
      is_current: false,
      supersedes_doc_id: null,
      content_type: 'notice_body',
      source_url: 'https://www.sgu.edu.cn/jwb/info/1050/29001.htm',
    },
    {
      doc_id: 'jwb-jxtz-2024-27810',
      title: '韶关学院学生学籍管理规定',
      publish_date: '2024-09-01',
      effective_from: '2024-09-01',
      effective_to: null,
      doc_version: 'v2024.1',
      is_current: true,
      supersedes_doc_id: null,
      content_type: 'notice_body',
      source_url: 'https://www.sgu.edu.cn/jwb/info/1050/27810.htm',
    },
  ],
  total: 3,
  page: 1,
  page_size: 20,
}

export const mockChatAnswer: ChatResponse = {
  answer:
    '2026年转专业面向全日制本科一年级、二年级学生，要求已修课程平均学分绩点不低于2.5。报名与考核安排见通知正文，受理时间截至2026年5月31日。',
  refused: false,
  refuse_reason: null,
  citations,
  retrieval: {
    mode: 'auto',
    applied_filters: [
      'effective_from<=2026-11-08',
      'effective_to>=2026-11-08 OR effective_to IS NULL',
    ],
    candidates: 42,
  },
  latency_ms: 812,
  answered_at: '2026-11-08T14:00:00+08:00',
}

/** 拒答样例：让"未找到依据"这条路径在 Mock 期就能被点到 */
export const mockChatRefused: ChatResponse = {
  answer:
    '未在教学通知中找到可回答该问题的现行依据。可能原因：该事项本年度未发布通知，或相关通知已过期。建议直接查阅教务处网站。',
  refused: true,
  refuse_reason: 'all_stale',
  citations: [],
  retrieval: { mode: 'auto', applied_filters: [], candidates: 0 },
  latency_ms: 143,
  answered_at: '2026-11-08T14:01:00+08:00',
}

export const mockEvalSummary: EvalSummary = {
  run_id: 'run-20261108-01',
  created_at: '2026-11-08T12:00:00+08:00',
  dataset_size: 40,
  metrics: {
    faithfulness: 0.87,
    answer_relevancy: 0.81,
    context_precision: 0.74,
    context_recall: 0.69,
    stale_recall_rate: 0.08,
    refusal_rate: 0.9,
  },
  by_year: [
    { year: 2026, stale_recall_rate: 0.05, count: 18 },
    { year: 2025, stale_recall_rate: 0.11, count: 9 },
    { year: 2024, stale_recall_rate: 0.09, count: 7 },
  ],
  regressions: [{ metric: 'context_precision', delta: -0.06, vs_run: 'run-20261101-01' }],
}

export const mockHealthz: Healthz = {
  status: 'ok',
  version: '0.1.0',
  components: { postgres: 'ok', rag_core: 'ok', redis: 'ok' },
}
