import { ApiError, getToken, request } from './client'
import { mockChatAnswer, mockChatRefused } from './mock/fixtures'
import { useMock } from './mode'
import type {
  ChatRequest,
  ChatResponse,
  Citation,
  RefuseReason,
  RetrievalMeta,
  StreamDeltaEvent,
  StreamDoneEvent,
  StreamErrorEvent,
  StreamMetaEvent,
  StreamSourcesEvent,
} from './types'

/** §3 非流式问答 */
export async function askChat(req: ChatRequest): Promise<ChatResponse> {
  if (useMock()) {
    await sleep(400)
    // 用关键词触发拒答分支，方便调试空态
    return /不存在|没有|未知/.test(req.query) ? mockChatRefused : mockChatAnswer
  }
  return request<ChatResponse>('/v1/chat', {
    method: 'POST',
    body: {
      query: req.query,
      session_id: req.session_id ?? null,
      top_k: req.top_k ?? 8,
      time_filter: {
        mode: 'auto',
        date_from: null,
        date_to: null,
        as_of: null,
        ...req.time_filter,
      },
    },
    timeoutMs: 15_000,
  })
}

export interface StreamHandlers {
  onMeta?: (e: StreamMetaEvent) => void
  onSources?: (e: StreamSourcesEvent) => void
  onDelta?: (text: string) => void
  onDone?: (e: StreamDoneEvent) => void
  onError?: (e: ApiError | StreamErrorEvent) => void
}

export interface StreamHandle {
  /** 主动断开；C 侧会在 2s 内取消上游请求 */
  close: () => void
}

/**
 * §4 SSE。契约是 GET + ?q=&token=，因为 EventSource 不能设请求头。
 * 超时/错误统一走 onError，且保证只回调一次。
 */
export function streamChat(query: string, handlers: StreamHandlers, topK = 8): StreamHandle {
  if (useMock()) return mockStream(query, handlers)

  const base = import.meta.env.VITE_API_BASE_URL ?? '/api'
  const sp = new URLSearchParams({ q: query, top_k: String(topK) })
  const token = getToken()
  if (token) sp.append('token', token)

  const es = new EventSource(`${base}/v1/chat/stream?${sp.toString()}`)
  let finished = false

  const finish = (fn: () => void) => {
    if (finished) return
    finished = true
    fn()
    es.close()
  }

  es.addEventListener('meta', (ev) => {
    try {
      handlers.onMeta?.(JSON.parse((ev as MessageEvent).data) as StreamMetaEvent)
    } catch {
      /* 单条坏事件不该中断整条流 */
    }
  })
  es.addEventListener('sources', (ev) => {
    try {
      handlers.onSources?.(JSON.parse((ev as MessageEvent).data) as StreamSourcesEvent)
    } catch {
      /* 同上 */
    }
  })
  es.addEventListener('delta', (ev) => {
    try {
      const data = JSON.parse((ev as MessageEvent).data) as StreamDeltaEvent
      handlers.onDelta?.(data.text)
    } catch {
      /* 同上 */
    }
  })
  es.addEventListener('done', (ev) => {
    let data: StreamDoneEvent = { latency_ms: 0, refused: false }
    try {
      data = JSON.parse((ev as MessageEvent).data) as StreamDoneEvent
    } catch {
      /* 用默认值收尾 */
    }
    finish(() => handlers.onDone?.(data))
  })
  es.addEventListener('error', (ev) => {
    // 服务端显式发来的 error 事件带 data；浏览器断线(onerror) 没有 data
    const raw = (ev as MessageEvent).data
    if (typeof raw === 'string' && raw) {
      try {
        const data = JSON.parse(raw) as StreamErrorEvent
        finish(() => handlers.onError?.(data))
        return
      } catch {
        /* 落到下面按断线处理 */
      }
    }
    finish(() => handlers.onError?.(new ApiError(50201, '回答中断，请重试', 0)))
  })

  return { close: () => finish(() => undefined) }
}

// ---------- Mock：按句吐字，模拟真实节奏 ----------

function mockStream(query: string, handlers: StreamHandlers): StreamHandle {
  const refused = /不存在|没有|未知/.test(query)
  const full = refused ? mockChatRefused.answer : mockChatAnswer.answer
  const retrieval: RetrievalMeta = refused
    ? { mode: 'auto', applied_filters: [], candidates: 0 }
    : mockChatAnswer.retrieval
  const citations: Citation[] = refused ? [] : mockChatAnswer.citations
  const pieces = refused ? [full] : (full.match(/[^。；！？]+[。；！？]?/g) ?? [full])

  let closed = false
  const timers: ReturnType<typeof setTimeout>[] = []

  const at = (ms: number, fn: () => void) => {
    timers.push(setTimeout(() => !closed && fn(), ms))
  }

  at(0, () => handlers.onMeta?.({ session_id: 'mock-session', retrieval }))
  at(120, () => handlers.onSources?.({ citations }))
  pieces.forEach((p, idx) => at(220 + idx * 180, () => handlers.onDelta?.(p)))
  at(220 + pieces.length * 180 + 80, () =>
    handlers.onDone?.({
      latency_ms: 300 + pieces.length * 180,
      refused,
      refuse_reason: refused ? ('all_stale' as RefuseReason) : null,
    }),
  )

  return {
    close: () => {
      closed = true
      timers.forEach(clearTimeout)
    },
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
