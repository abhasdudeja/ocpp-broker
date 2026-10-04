import { describe, expect, it } from 'vitest'

import { formatDateTime, formatUptime } from './format'

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
