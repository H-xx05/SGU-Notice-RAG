import { request, setToken } from './client'
import { mockToken } from './mock/fixtures'
import { useMock } from './mode'
import type { TokenResponse } from './types'

/**
 * §1 仅开发/演示用。生产返回 40301 —— 届时改用学校统一身份认证（不在 v1 范围）。
 */
export async function fetchToken(
  userId: string,
  role: 'student' | 'admin' = 'student',
): Promise<TokenResponse> {
  if (useMock()) return mockToken
  const data = await request<TokenResponse>('/v1/auth/token', {
    method: 'POST',
    body: { user_id: userId, role },
    auth: false,
  })
  setToken(data.token)
  return data
}
