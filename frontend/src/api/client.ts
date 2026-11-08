import {
  CODE_OK,
  CODE_RAG_TIMEOUT,
  CODE_RAG_UNAVAILABLE,
  CODE_RATE_LIMITED,
  type Envelope,
} from './types'

/**
 * 唯一 HTTP 入口。所有请求函数都走这里：
 *  - baseURL 只从 VITE_API_BASE_URL 读（默认 /api）
 *  - 统一拆包 {code,message,data}，非 0 抛 ApiError
 *  - 统一处理 401（清 token）/ 429（带 Retry-After）
 */

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api'

const TOKEN_KEY = 'sgu.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null // 隐私模式 / SSR
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 忽略：无存储权限时退化为仅在内存中有效 */
  }
}

export class ApiError extends Error {
  readonly code: number
  readonly httpStatus: number
  readonly retryAfter: number | null

  constructor(code: number, message: string, httpStatus: number, retryAfter: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.httpStatus = httpStatus
    this.retryAfter = retryAfter
  }

  /** 是否是"暂时性失败"，值得给用户一个重试按钮 */
  get transient(): boolean {
    return (
      this.code === CODE_RAG_UNAVAILABLE ||
      this.code === CODE_RAG_TIMEOUT ||
      this.code === CODE_RATE_LIMITED ||
      this.httpStatus >= 500
    )
  }
}

function parseRetryAfter(raw: string | null): number | null {
  if (!raw) return null
  const n = Number(raw)
  return Number.isFinite(n) && n >= 0 ? n : null
}

export interface RequestOptions {
  method?: 'GET' | 'POST'
  query?: Record<string, unknown>
  body?: unknown
  signal?: AbortSignal
  /** 默认 30s；SSE 用不到这个值 */
  timeoutMs?: number
  auth?: boolean
}

function buildUrl(path: string, query?: Record<string, unknown>): string {
  const url = `${BASE_URL}${path}`
  if (!query) return url
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue
    sp.append(k, String(v))
  }
  const qs = sp.toString()
  return qs ? `${url}?${qs}` : url
}

/** 发请求并拆包。失败一律抛 ApiError。 */
export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', query, body, signal, timeoutMs = 30_000, auth = true } = opts

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  // 自己控超时：AbortSignal.timeout 在旧 WebView 上没有
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(new DOMException('timeout', 'TimeoutError')), timeoutMs)
  const onAbort = () => ctrl.abort(signal?.reason)
  signal?.addEventListener('abort', onAbort, { once: true })

  let res: Response
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'TimeoutError') {
      throw new ApiError(CODE_RAG_TIMEOUT, '请求超时，请稍后重试', 504)
    }
    throw new ApiError(CODE_RAG_UNAVAILABLE, '网络异常，请检查网络后重试', 0)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }

  const retryAfter = parseRetryAfter(res.headers.get('Retry-After'))

  let payload: Envelope<T> | null = null
  try {
    payload = (await res.json()) as Envelope<T>
  } catch {
    // 网关返回 HTML 错误页时走到这里
    if (!res.ok) {
      throw new ApiError(
        CODE_RAG_UNAVAILABLE,
        `服务暂不可用（HTTP ${res.status}）`,
        res.status,
        retryAfter,
      )
    }
    throw new ApiError(CODE_RAG_UNAVAILABLE, '响应格式异常', res.status)
  }

  if (payload.code !== CODE_OK) {
    throw new ApiError(payload.code, payload.message || '请求失败', res.status, retryAfter)
  }
  return payload.data
}
