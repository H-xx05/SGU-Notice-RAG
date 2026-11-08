import { request } from './client'
import { mockNotices } from './mock/fixtures'
import { useMock } from './mode'
import type { NoticePage, NoticeQuery } from './types'

/** §2 通知列表，支持时间范围过滤 */
export async function fetchNotices(query: NoticeQuery = {}): Promise<NoticePage> {
  if (useMock()) return filterMock(query)
  return request<NoticePage>('/v1/notices', { query: { ...query } })
}

function filterMock(q: NoticeQuery): NoticePage {
  let items = [...mockNotices.items]
  if (q.include_stale === false) items = items.filter((n) => n.is_current)
  if (q.year !== undefined) items = items.filter((n) => n.publish_date.startsWith(String(q.year)))
  if (q.q) {
    const needle = q.q.trim()
    if (needle) items = items.filter((n) => n.title.includes(needle))
  }
  if (q.content_type) items = items.filter((n) => n.content_type === q.content_type)
  if (q.publish_from) items = items.filter((n) => n.publish_date >= q.publish_from!)
  if (q.publish_to) items = items.filter((n) => n.publish_date <= q.publish_to!)

  const page = q.page ?? 1
  const pageSize = q.page_size ?? 20
  const start = (page - 1) * pageSize
  return {
    items: items.slice(start, start + pageSize),
    total: items.length,
    page,
    page_size: pageSize,
  }
}
