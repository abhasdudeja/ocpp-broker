import { useState } from 'react'

export interface Shown<T> {
  visible: T[]
  hidden: number
  showMore: () => void
}

/**
 * The first ``step`` of ``items``, and a way to reveal ``step`` more. A page with thousands of rows renders and updates
 * a few hundred at a time. A different ``resetKey`` (a new search, say) starts again from the first ``step``.
 */
export function useShowMore<T>(items: T[], resetKey: string, step = 100): Shown<T> {
  const [reveal, setReveal] = useState({ key: resetKey, count: step })
  const count = reveal.key === resetKey ? reveal.count : step
  return {
    visible: items.slice(0, count),
    hidden: Math.max(0, items.length - count),
    showMore: () => setReveal({ key: resetKey, count: count + step }),
  }
}
