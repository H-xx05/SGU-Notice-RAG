/**
 * 客户端的错误映射。这是 D 组唯一需要精确测逻辑的地方 ——
 * 其余都是渲染，改坏了一眼能看见；错误码映射改坏了只在网络差的时候出问题。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, request, setToken } from '../client'
import { CODE_OK, CODE_RATE_LIMITED, CODE_RAG_TIMEOUT } from '../types'

const fetchMock = vi.fn()

function jsonResponse(
  body: unknown,
  init: { status?: number; headers?: Record<string, string> } = {},
) {
  return {
    ok: (init.status ?? 200) < 400,
    status: init.status ?? 200,
    headers: new Headers(init.headers ?? {}),
    json: async () => body,
  } as unknown as Response
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('request', () => {
  it('成功时拆包返回 data', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ code: CODE_OK, message: 'ok', data: { a: 1 } }))
    await expect(request<{ a: number }>('/v1/x')).resolves.toEqual({ a: 1 })
  })

  it('业务码非 0 抛 ApiError 并带 code', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ code: 40001, message: 'query 不能为空', data: null }, { status: 400 }),
    )
    await expect(request('/v1/x')).rejects.toMatchObject({ code: 40001, message: 'query 不能为空' })
  })

  it('429 带 Retry-After 时解析成秒数，且 transient=true', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { code: CODE_RATE_LIMITED, message: '请求过于频繁', data: null },
        { status: 429, headers: { 'Retry-After': '17' } },
      ),
    )
    const err = await request('/v1/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).retryAfter).toBe(17)
    expect((err as ApiError).transient).toBe(true)
  })

  it('网关返回 HTML 错误页时给出可读错误，不是 "Unexpected token <"', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      headers: new Headers(),
      json: async () => {
        throw new SyntaxError('Unexpected token <')
      },
    } as unknown as Response)
    const err = await request('/v1/x').catch((e: unknown) => e)
    expect((err as ApiError).message).toContain('502')
    expect((err as ApiError).transient).toBe(true)
  })

  it('超时映射为 50202 且消息面向用户', async () => {
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => {
            reject(new DOMException('timeout', 'TimeoutError'))
          })
        }),
    )
    const err = await request('/v1/x', { timeoutMs: 5 }).catch((e: unknown) => e)
    expect((err as ApiError).code).toBe(CODE_RAG_TIMEOUT)
    expect((err as ApiError).message).toContain('超时')
  })

  it('自动带 Bearer token', async () => {
    setToken('abc.def')
    fetchMock.mockResolvedValue(jsonResponse({ code: 0, message: 'ok', data: null }))
    await request('/v1/x')
    const init = fetchMock.mock.calls[0]![1] as RequestInit
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer abc.def')
  })

  it('auth:false 时不带 token（取 token 接口本身不能要求鉴权）', async () => {
    setToken('abc.def')
    fetchMock.mockResolvedValue(jsonResponse({ code: 0, message: 'ok', data: null }))
    await request('/v1/auth/token', { method: 'POST', auth: false })
    const init = fetchMock.mock.calls[0]![1] as RequestInit
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined()
  })

  it('query 里 null/undefined/空串被丢掉，不进 URL', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ code: 0, message: 'ok', data: null }))
    await request('/v1/notices', { query: { page: 1, q: '', year: null, foo: undefined } })
    const url = fetchMock.mock.calls[0]![0] as string
    expect(url).toContain('page=1')
    expect(url).not.toContain('q=')
    expect(url).not.toContain('year=')
    expect(url).not.toContain('foo=')
  })
})
