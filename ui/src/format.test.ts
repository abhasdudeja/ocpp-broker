import { describe, expect, it } from 'vitest'

import { formatDateTime, formatRelative, formatUptime } from './format'

describe('formatRelative', () => {
  const now = Date.parse('2026-10-04T12:00:00Z')
  it.each([
    ['2026-10-04T12:00:00Z', 'just now'],
    ['2026-10-04T11:59:57Z', 'just now'],
    ['2026-10-04T11:59:30Z', '30s ago'],
    ['2026-10-04T11:58:00Z', '2m ago'],
    ['2026-10-04T09:00:00Z', '3h ago'],
    ['2026-10-01T12:00:00Z', '3d ago'],
    ['2026-10-04T12:00:30Z', 'just now'],
  ])('%s is %s', (iso, text) => {
    expect(formatRelative(iso, now)).toBe(text)
  })

  it('says never when there is no time, and leaves an unreadable one as it is', () => {
    expect(formatRelative(null, now)).toBe('never')
    expect(formatRelative(undefined, now)).toBe('never')
    expect(formatRelative('soon', now)).toBe('soon')
  })
})

describe('formatUptime', () => {
  it.each([
    [0, '0s'],
    [59.9, '59s'],
    [61, '1m 1s'],
    [3600, '1h 0m'],
    [5231.4, '1h 27m'],
    [93784, '1d 2h 3m'],
    [-5, '0s'],
  ])('%s seconds is %s', (seconds, text) => {
    expect(formatUptime(seconds)).toBe(text)
  })
})

describe('formatDateTime', () => {
  it('leaves something that is not a date as it is', () => {
    expect(formatDateTime('not a date')).toBe('not a date')
  })
})
