import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { ApiError, type BrokerEvent } from './api/client'
import { readSse } from './api/sse'
import { useAuth } from './auth'

/**
 * connecting: first attempt; live: the stream is open; reconnecting: it dropped and is being retried;
 * unavailable: this broker has no event stream (older version), so pages rely on polling alone.
 */
export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'unavailable'

/** Sent to listeners when events may have been missed (new broker run, or a gap too long to catch up): reload what you show. */
export interface StreamReset {
  type: 'stream.reset'
}
export type StreamEvent = BrokerEvent | StreamReset

interface Events {
  status: StreamStatus
  /** Newest first, at most RECENT_LIMIT. */
  recent: BrokerEvent[]
  /** Called for every event as it arrives; returns the way to stop listening. */
  subscribe: (listener: (event: StreamEvent) => void) => () => void
}

export const RECENT_LIMIT = 200
const REPLAY = 100 // events asked for on the first connection, so the feed is not empty
const FLUSH_MS = 150 // how often the feed is redrawn during a burst
const BACKOFF_START_MS = 1_000
const BACKOFF_MAX_MS = 30_000
const STALL_MS = 45_000 // the broker sends a keepalive every 15 s; silence for this long means the link is dead

const EventsContext = createContext<Events | undefined>(undefined)

function isEvent(value: unknown): value is BrokerEvent {
  if (!value || typeof value !== 'object') return false
  const e = value as Record<string, unknown>
  return typeof e.id === 'number' && typeof e.type === 'string' && typeof e.data === 'object' && e.data !== null
}

export function EventsProvider({ children }: { children: ReactNode }) {
  const { key, keyRejected } = useAuth()
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const [recent, setRecent] = useState<BrokerEvent[]>([])
  const listeners = useRef(new Set<(event: StreamEvent) => void>())

  const subscribe = useCallback((listener: (event: StreamEvent) => void) => {
    listeners.current.add(listener)
    return () => {
      listeners.current.delete(listener)
    }
  }, [])

  useEffect(() => {
    if (!key) return
    const controller = new AbortController()
    let lastId: string | null = null
    let instance: string | null = null
    let backoff = BACKOFF_START_MS
    let buffer: BrokerEvent[] = []
    let flushTimer: number | undefined
    let sleepTimer: number | undefined

    const flush = () => {
      flushTimer = undefined
      const batch = buffer
      buffer = []
      if (batch.length > 0) setRecent((previous) => [...batch.reverse(), ...previous].slice(0, RECENT_LIMIT))
    }
    const notify = (event: StreamEvent) => {
      for (const listener of [...listeners.current]) {
        try {
          listener(event)
        } catch (error) {
          console.error('An event listener failed', error)
        }
      }
    }

    /** One connection. Returns when the stream ends; throws if it could not be opened or stalled. */
    async function connect(): Promise<void> {
      const attempt = new AbortController()
      const stop = () => attempt.abort()
      controller.signal.addEventListener('abort', stop)
      let watchdog: number | undefined
      const feed = () => {
        window.clearTimeout(watchdog)
        watchdog = window.setTimeout(stop, STALL_MS)
      }
      try {
        feed()
        const headers: Record<string, string> = { 'X-API-Key': key ?? '', Accept: 'text/event-stream' }
        if (lastId !== null) headers['Last-Event-ID'] = lastId
        const response = await fetch(`/api/events?replay=${REPLAY}`, { headers, cache: 'no-store', signal: attempt.signal })
        if (!response.ok || !response.body) throw new ApiError(response.status, response.statusText || `HTTP ${response.status}`)
        for await (const item of readSse(response.body)) {
          feed()
          if (item.kind !== 'message') continue
          let parsed: unknown
          try {
            parsed = JSON.parse(item.data)
          } catch {
            continue // a message this console cannot read is skipped, not fatal
          }
          if (item.event === 'stream.open') {
            const open = parsed as { instance_id?: unknown; missed?: unknown }
            const run = typeof open.instance_id === 'string' ? open.instance_id : null
            const anotherRun = instance !== null && run !== null && run !== instance
            if (anotherRun) {
              buffer = []
              setRecent([])
              lastId = null
            }
            instance = run
            backoff = BACKOFF_START_MS
            setStatus('live')
            if (anotherRun || open.missed === true) notify({ type: 'stream.reset' })
          } else if (isEvent(parsed)) {
            lastId = item.id ?? String(parsed.id)
            buffer.push(parsed)
            if (flushTimer === undefined) flushTimer = window.setTimeout(flush, FLUSH_MS)
            notify(parsed)
          }
        }
      } finally {
        window.clearTimeout(watchdog)
        controller.signal.removeEventListener('abort', stop)
      }
    }

    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        sleepTimer = window.setTimeout(resolve, ms)
      })

    async function run() {
      while (!controller.signal.aborted) {
        try {
          await connect()
        } catch (error) {
          if (controller.signal.aborted) return
          if (error instanceof ApiError && error.status === 401) {
            keyRejected()
            return
          }
          if (error instanceof ApiError && error.status === 404) {
            setStatus('unavailable') // this broker has no event stream; pages keep polling
            return
          }
          // anything else (a network failure, a stream that broke, a 503 when the broker is full): retried below
        }
        if (controller.signal.aborted) return
        setStatus('reconnecting')
        await sleep(backoff)
        backoff = Math.min(backoff * 2, BACKOFF_MAX_MS)
      }
    }
    void run()

    return () => {
      controller.abort()
      window.clearTimeout(flushTimer)
      window.clearTimeout(sleepTimer)
    }
  }, [key, keyRejected])

  const value = useMemo(() => ({ status, recent, subscribe }), [status, recent, subscribe])
  return <EventsContext.Provider value={value}>{children}</EventsContext.Provider>
}

export function useEvents(): Events {
  const events = useContext(EventsContext)
  if (!events) throw new Error('useEvents must be used inside <EventsProvider>')
  return events
}

/**
 * Run ``reload`` shortly after an event that ``matches`` (or after a reset), at most once per ``delayMs``,
 * so a burst of events makes one refresh. ``matches`` may change between renders.
 */
export function useRefreshOnEvents(reload: () => void, matches: (event: BrokerEvent) => boolean, delayMs = 300): void {
  const { subscribe } = useEvents()
  const latest = useRef({ reload, matches })
  useEffect(() => {
    latest.current = { reload, matches }
  })
  useEffect(() => {
    let timer: number | undefined
    const stop = subscribe((event) => {
      if (event.type !== 'stream.reset' && !latest.current.matches(event as BrokerEvent)) return
      if (timer === undefined) {
        timer = window.setTimeout(() => {
          timer = undefined
          latest.current.reload()
        }, delayMs)
      }
    })
    return () => {
      stop()
      window.clearTimeout(timer)
    }
  }, [subscribe, delayMs])
}
