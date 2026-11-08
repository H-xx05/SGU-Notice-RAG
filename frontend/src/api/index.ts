/**
 * 前端唯一的 API 出口。组件里 **不要** 直接 import './client' 或硬编码 fetch。
 * 所有路径都能在 docs/api.md 里逐条查到。
 */
export * from './types'
export { ApiError, getToken, setToken, request } from './client'
export { useMock } from './mode'
export { fetchToken } from './auth'
export { fetchNotices } from './notices'
export { askChat, streamChat } from './chat'
export type { StreamHandle, StreamHandlers } from './chat'
export { fetchEvalSummary, emptyEvalSummary } from './eval'
