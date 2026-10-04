import type { MeterReading } from './api/client'

export const RANGES = [
  { id: '1h', label: 'Last hour', ms: 3_600_000 },
  { id: '24h', label: 'Last 24 hours', ms: 86_400_000 },
  { id: '7d', label: 'Last 7 days', ms: 7 * 86_400_000 },
  { id: '30d', label: 'Last 30 days', ms: 30 * 86_400_000 },
] as const

/** The ``since`` time (ISO 8601) a range id stands for, counted back from ``now``; nothing for "all time". */
export function sinceFor(range: string, now: number): string | undefined {
  const found = RANGES.find((r) => r.id === range)
  return found ? new Date(now - found.ms).toISOString() : undefined
}

/** "2h 5m", "45s": how long a transaction ran (or has run so far, when it has no end). */
export function formatDuration(startIso: string | null, endIso: string | null, now: number): string {
  if (!startIso) return '—'
  const start = new Date(startIso).getTime()
  const end = endIso ? new Date(endIso).getTime() : now
  if (Number.isNaN(start) || Number.isNaN(end)) return '—'
  const total = Math.max(0, Math.round((end - start) / 1000))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${total % 60}s`
  return `${total}s`
}

/** Watt-hours as "850 Wh" or "12.35 kWh". */
export function formatEnergy(wh: number | null): string {
  if (wh === null) return '—'
  return Math.abs(wh) >= 10_000 ? `${(wh / 1000).toFixed(2)} kWh` : `${wh} Wh`
}

export interface Point {
  t: number
  v: number
}

export interface Series {
  /** What tells the series apart: measurand, phase and unit */
  id: string
  label: string
  unit: string
  points: Point[]
}

/**
 * The readings of a transaction as one series per measurand/phase/unit, each oldest first. A reading that
 * has no time or whose value is not a number cannot be placed on a line and is left out.
 */
export function buildSeries(readings: MeterReading[]): Series[] {
  const byId = new Map<string, Series>()
  for (const reading of readings) {
    if (reading.value === null || !reading.timestamp) continue
    const t = new Date(reading.timestamp).getTime()
    if (Number.isNaN(t)) continue
    const id = `${reading.measurand}|${reading.phase ?? ''}|${reading.unit}`
    let series = byId.get(id)
    if (!series) {
      series = { id, label: reading.phase ? `${reading.measurand} (${reading.phase})` : reading.measurand, unit: reading.unit, points: [] }
      byId.set(id, series)
    }
    series.points.push({ t, v: reading.value })
  }
  const all = [...byId.values()]
  for (const series of all) series.points.sort((a, b) => a.t - b.t)
  // The energy register first: it is what a transaction is about
  return all.sort((a, b) => Number(b.label.startsWith('Energy.Active.Import')) - Number(a.label.startsWith('Energy.Active.Import')) || a.label.localeCompare(b.label))
}
