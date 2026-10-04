import { useCallback, useEffect, useState } from 'react'

import { apiGet, isAbort, withQuery } from './api/client'
import { useAuth } from './auth'

interface Page<T> {
  available: boolean
  reason: string | null
  next_cursor: string | null
  items: T[]
}

interface State<T> {
  /** The request this state answers; a different one means the screen is waiting for new data */
  query: string
  items: T[]
  next: string | null
  available: boolean
  reason: string | null
  error: Error | null
}

export interface Pages<T> {
  items: T[]
  available: boolean
  reason: string | null
  error: Error | null
  loading: boolean
  loadingMore: boolean
  hasMore: boolean
  more: () => void
  reload: () => void
}

const PAGE = '50'

/**
 * A newest-first list read a page at a time from a history endpoint: the first page for ``params``, then
 * ``more()`` appends the next. A different ``path`` or ``params`` starts over (the old rows stay until the
 * new ones arrive, so the table does not flash).
 */
export function usePages<T>(path: string, params: Record<string, string | undefined>): Pages<T> {
  const { key } = useAuth()
  const query = withQuery(path, { ...params, limit: PAGE })
  const [state, setState] = useState<State<T> | null>(null)
  const [round, setRound] = useState(0)
  const [more, setMore] = useState(false)
  const stamp = `${query}#${round}`
  const reload = useCallback(() => setRound((n) => n + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    apiGet<Page<T>>(query, key ?? '', controller.signal)
      .then((page) => setState({ query: stamp, items: page.items, next: page.next_cursor, available: page.available, reason: page.reason, error: null }))
      .catch((error: unknown) => {
        if (isAbort(error)) return
        setState((previous) => ({
          query: stamp,
          items: previous?.items ?? [],
          next: null,
          available: previous?.available ?? true,
          reason: previous?.reason ?? null,
          error: error instanceof Error ? error : new Error(String(error)),
        }))
      })
    return () => controller.abort()
  }, [query, stamp, key])

  const next = state?.next ?? null
  const loadMore = useCallback(() => {
    if (!next || more) return
    setMore(true)
    apiGet<Page<T>>(`${query}${query.includes('?') ? '&' : '?'}cursor=${encodeURIComponent(next)}`, key ?? '')
      // An answer for a list the screen has since left is not appended to the one it shows now
      .then((page) => setState((previous) => (previous?.query === stamp ? { ...previous, items: [...previous.items, ...page.items], next: page.next_cursor, error: null } : previous)))
      .catch((error: unknown) => setState((previous) => (previous?.query === stamp ? { ...previous, error: error instanceof Error ? error : new Error(String(error)) } : previous)))
      .finally(() => setMore(false))
  }, [next, more, query, stamp, key])

  return {
    items: state?.items ?? [],
    available: state?.available ?? true,
    reason: state?.reason ?? null,
    error: state?.error ?? null,
    loading: state === null || state.query !== stamp,
    loadingMore: more,
    hasMore: next !== null,
    more: loadMore,
    reload,
  }
}
