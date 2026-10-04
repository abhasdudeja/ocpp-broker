import { useEffect, useState } from 'react'

/** The current time in milliseconds, refreshed every ``intervalMs`` so "5s ago" keeps counting between data refreshes. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}
