import { useState } from 'react'

import { formatDateTime } from '../format'
import type { Series } from '../history'

const WIDTH = 640
const HEIGHT = 240
const PAD = { left: 64, right: 16, top: 12, bottom: 34 }

function nice(value: number): string {
  return Math.abs(value) >= 1000 ? value.toFixed(0) : String(Math.round(value * 100) / 100)
}

/**
 * One line chart of a transaction's meter readings, a series at a time. The readings are also listed in a
 * table below it, so nothing is only in the picture.
 */
export function MeterChart({ series }: { series: Series[] }) {
  const [chosen, setChosen] = useState(series[0]?.id)
  const current = series.find((s) => s.id === chosen) ?? series[0]
  const points = current?.points ?? []
  const first = points[0]
  const last = points[points.length - 1]
  if (!current || !first || !last) return <p className="empty">No numeric meter readings were recorded for this transaction.</p>

  const t0 = first.t
  const t1 = last.t
  const values = points.map((p) => p.v)
  let lo = Math.min(...values)
  let hi = Math.max(...values)
  if (lo === hi) {
    lo -= 1
    hi += 1
  }
  const x = (t: number) => PAD.left + (t1 === t0 ? (WIDTH - PAD.left - PAD.right) / 2 : ((t - t0) / (t1 - t0)) * (WIDTH - PAD.left - PAD.right))
  const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo)) * (HEIGHT - PAD.top - PAD.bottom)
  const description = `${current.label}, ${points.length} reading${points.length === 1 ? '' : 's'} from ${formatDateTime(new Date(t0).toISOString())} to ${formatDateTime(new Date(t1).toISOString())}, from ${nice(Math.min(...values))} to ${nice(Math.max(...values))} ${current.unit}`

  return (
    <div className="chart">
      {series.length > 1 && (
        <div className="filters">
          <label className="visually-hidden" htmlFor="chart-series">
            Measurement
          </label>
          <select id="chart-series" value={current.id} onChange={(event) => setChosen(event.target.value)}>
            {series.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label} ({s.unit})
              </option>
            ))}
          </select>
        </div>
      )}
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={description} className="chart-svg">
        <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={HEIGHT - PAD.bottom} className="chart-axis" />
        <line x1={PAD.left} y1={HEIGHT - PAD.bottom} x2={WIDTH - PAD.right} y2={HEIGHT - PAD.bottom} className="chart-axis" />
        <text x={PAD.left - 8} y={PAD.top + 4} textAnchor="end" className="chart-label">
          {nice(hi)}
        </text>
        <text x={PAD.left - 8} y={HEIGHT - PAD.bottom} textAnchor="end" className="chart-label">
          {nice(lo)}
        </text>
        <text x={PAD.left - 8} y={(HEIGHT - PAD.bottom + PAD.top) / 2} textAnchor="end" className="chart-label">
          {current.unit}
        </text>
        <text x={PAD.left} y={HEIGHT - 10} textAnchor="start" className="chart-label">
          {new Date(t0).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </text>
        <text x={WIDTH - PAD.right} y={HEIGHT - 10} textAnchor="end" className="chart-label">
          {new Date(t1).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </text>
        <polyline points={points.map((p) => `${x(p.t)},${y(p.v)}`).join(' ')} className="chart-line" fill="none" />
        {points.length <= 80 && points.map((p, i) => <circle key={`${p.t}-${i}`} cx={x(p.t)} cy={y(p.v)} r="3" className="chart-dot" />)}
      </svg>
      <details>
        <summary>The readings as a table</summary>
        <div className="table-wrap">
          <table className="table">
            <caption className="visually-hidden">{current.label} readings</caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">{current.unit}</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p, i) => (
                <tr key={`${p.t}-${i}`}>
                  <td>{formatDateTime(new Date(p.t).toISOString())}</td>
                  <td>{nice(p.v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  )
}
