import { useCallback, useEffect, useRef, useState } from 'react'

import { isAbort } from './api/client'

interface PolledState<T> {
  /** The latest successful result; kept while a refresh fails, so the page does not go blank. */
  data: T | null
  /** The error of the latest refresh, or null if it worked. */
  error: Error | null
  updatedAt: Date | null
}

export interface Polled<T> extends PolledState<T> {
  /** Load again now (an event said something changed), then carry on polling from there. */
  reload: () => void
}

/**
 * Call ``load`` now and then every ``intervalMs`` after each call finishes, until the component goes away.
 * A different ``resetKey`` (say, a changed search) starts over at once; the previous result stays on screen
 * until the new one arrives. ``reload`` starts over the same way.
 */
export function usePolling<T>(
  load: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  resetKey = '',
): Polled<T> {
  const [state, setState] = useState<PolledState<T>>({ data: null, error: null, updatedAt: null })
  const [round, setRound] = useState(0)
  const reload = useCallback(() => setRound((n) => n + 1), [])
  const latest = useRef(load)
  useEffect(() => {
    latest.current = load
  })

  useEffect(() => {
    const controller = new AbortController()
    let timer: number | undefined
    let stopped = false

    const tick = async () => {
      try {
        const data = await latest.current(controller.signal)
        if (!stopped) setState({ data, error: null, updatedAt: new Date() })
      } catch (error) {
        if (stopped || isAbort(error)) return
        setState((previous) => ({ ...previous, error: error instanceof Error ? error : new Error(String(error)) }))
      }
      if (!stopped) timer = window.setTimeout(tick, intervalMs)
    }
    void tick()

    return () => {
      stopped = true
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [intervalMs, resetKey, round])

  return { ...state, reload }
}
