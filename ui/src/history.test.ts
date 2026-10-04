import { describe, expect, it } from 'vitest'

import type { MeterReading } from './api/client'
import { buildSeries, formatDuration, formatEnergy, sinceFor } from './history'

const NOW = Date.parse('2026-10-04T12:00:00Z')

const reading = (overrides: Partial<MeterReading> = {}): MeterReading => ({
  timestamp: '2026-10-04T10:00:00Z',
  connector_id: 1,
  transaction_id: 7,
  measurand: 'Energy.Active.Import.Register',
  phase: null,
  unit: 'Wh',
  context: null,
  location: null,
  value: 100,
  raw_value: '100',
  ...overrides,
})

describe('time ranges', () => {
  it('counts back from the moment given', () => {
    expect(sinceFor('1h', NOW)).toBe('2026-10-04T11:00:00.000Z')
    expect(sinceFor('24h', NOW)).toBe('2026-10-03T12:00:00.000Z')
    expect(sinceFor('7d', NOW)).toBe('2026-09-27T12:00:00.000Z')
    expect(sinceFor('30d', NOW)).toBe('2026-09-04T12:00:00.000Z')
  })

  it('means all time for no range and for one it does not know', () => {
    expect(sinceFor('', NOW)).toBeUndefined()
    expect(sinceFor('forever', NOW)).toBeUndefined()
  })
})

describe('durations and energy', () => {
  it.each([
    ['2026-10-04T10:00:00Z', '2026-10-04T10:00:45Z', '45s'],
    ['2026-10-04T10:00:00Z', '2026-10-04T10:05:09Z', '5m 9s'],
    ['2026-10-04T10:00:00Z', '2026-10-04T12:05:00Z', '2h 5m'],
    ['2026-10-02T10:00:00Z', '2026-10-04T12:05:00Z', '2d 2h'],
    ['2026-10-04T12:00:00Z', '2026-10-04T10:00:00Z', '0s'],
  ])('a transaction from %s to %s lasted %s', (start, end, text) => {
    expect(formatDuration(start, end, NOW)).toBe(text)
  })

  it('counts a running transaction up to now, and shows nothing for a missing or unreadable time', () => {
    expect(formatDuration('2026-10-04T10:00:00Z', null, NOW)).toBe('2h 0m')
    expect(formatDuration(null, null, NOW)).toBe('—')
    expect(formatDuration('not a time', null, NOW)).toBe('—')
  })

  it('shows watt-hours, and kilowatt-hours from ten thousand', () => {
    expect(formatEnergy(null)).toBe('—')
    expect(formatEnergy(0)).toBe('0 Wh')
    expect(formatEnergy(850)).toBe('850 Wh')
    expect(formatEnergy(9999)).toBe('9999 Wh')
    expect(formatEnergy(12345)).toBe('12.35 kWh')
  })
})

describe('building the series of a transaction', () => {
  it('makes one series per measurand, phase and unit, each oldest first, with the energy register first', () => {
    const series = buildSeries([
      reading({ measurand: 'Power.Active.Import', unit: 'W', value: 7000, timestamp: '2026-10-04T10:10:00Z' }),
      reading({ measurand: 'Power.Active.Import', unit: 'W', value: 6900, timestamp: '2026-10-04T10:05:00Z' }),
      reading({ value: 1500, timestamp: '2026-10-04T10:05:00Z' }),
      reading({ measurand: 'Current.Import', unit: 'A', phase: 'L1', value: 10 }),
      reading({ measurand: 'Current.Import', unit: 'A', phase: 'L2', value: 11 }),
    ])
    expect(series.map((s) => s.label)).toEqual(['Energy.Active.Import.Register', 'Current.Import (L1)', 'Current.Import (L2)', 'Power.Active.Import'])
    const power = series[3]
    expect(power?.unit).toBe('W')
    expect(power?.points.map((p) => p.v)).toEqual([6900, 7000])
  })

  it('leaves out what cannot be drawn: no time, an unreadable time, a value that is not a number', () => {
    const series = buildSeries([reading({ timestamp: null }), reading({ timestamp: 'later' }), reading({ value: null, raw_value: 'n/a' }), reading({ value: 5 })])
    expect(series).toHaveLength(1)
    expect(series[0]?.points).toHaveLength(1)
  })

  it('has no series without readings', () => {
    expect(buildSeries([])).toEqual([])
  })

  it('keeps the same measurand in separate series when the unit differs', () => {
    const series = buildSeries([reading({ measurand: 'Voltage', unit: 'V', value: 230 }), reading({ measurand: 'Voltage', unit: 'kV', value: 0.23 })])
    expect(series.map((s) => s.unit).sort()).toEqual(['V', 'kV'])
  })
})
