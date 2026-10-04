import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { buildSeries, type Series } from '../history'
import { MeterChart } from './MeterChart'

const series = (id: string, label: string, unit: string, values: number[]): Series => ({
  id,
  label,
  unit,
  points: values.map((v, i) => ({ t: Date.parse('2026-10-04T10:00:00Z') + i * 300_000, v })),
})

const linePoints = () => (screen.getByRole('img').querySelector('polyline')?.getAttribute('points') ?? '')

describe('the meter chart', () => {
  it('draws a line through the readings and describes it for a screen reader', () => {
    render(<MeterChart series={[series('e', 'Energy.Active.Import.Register', 'Wh', [1000, 1500, 2100])]} />)
    const picture = screen.getByRole('img')
    expect(picture.getAttribute('aria-label')).toMatch(/Energy\.Active\.Import\.Register, 3 readings from .* to .*, from 1000 to 2100 Wh/)
    expect(linePoints().split(' ')).toHaveLength(3)
    expect(picture.querySelectorAll('circle')).toHaveLength(3)
  })

  it('puts the highest reading at the top and the lowest at the bottom', () => {
    render(<MeterChart series={[series('e', 'Power', 'W', [10, 30, 20])]} />)
    const ys = linePoints().split(' ').map((p) => Number(p.split(',')[1]))
    expect(ys[1]).toBeLessThan(ys[2] ?? 0)
    expect(ys[2]).toBeLessThan(ys[0] ?? 0)
  })

  it('draws a single reading, and a flat line, without dividing by zero', () => {
    const { unmount } = render(<MeterChart series={[series('e', 'Power', 'W', [5])]} />)
    expect(linePoints()).not.toMatch(/NaN|Infinity/)
    unmount()
    render(<MeterChart series={[series('e', 'Power', 'W', [5, 5, 5])]} />)
    expect(linePoints()).not.toMatch(/NaN|Infinity/)
  })

  it('lists the readings in a table as well', () => {
    render(<MeterChart series={[series('e', 'Power', 'W', [10, 30])]} />)
    const table = within(screen.getByRole('table', { hidden: true, name: 'Power readings' }))
    expect(table.getAllByRole('row', { hidden: true })).toHaveLength(3)
    expect(table.getByRole('cell', { hidden: true, name: '30' })).toBeInTheDocument()
  })

  it('lets the reader choose which measurement to draw', async () => {
    render(<MeterChart series={[series('e', 'Energy', 'Wh', [1, 2]), series('p', 'Power', 'W', [7, 8, 9])]} />)
    expect(screen.getByRole('img').getAttribute('aria-label')).toMatch(/^Energy, 2 readings/)
    await userEvent.selectOptions(screen.getByLabelText('Measurement'), 'p')
    expect(screen.getByRole('img').getAttribute('aria-label')).toMatch(/^Power, 3 readings/)
  })

  it('offers no choice when there is one measurement', () => {
    render(<MeterChart series={[series('e', 'Energy', 'Wh', [1, 2])]} />)
    expect(screen.queryByLabelText('Measurement')).not.toBeInTheDocument()
  })

  it('says so when there is nothing to draw', () => {
    render(<MeterChart series={buildSeries([])} />)
    expect(screen.getByText(/No numeric meter readings/)).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('keeps readings taken at the same moment', () => {
    const doubled: Series = {
      id: 'x',
      label: 'Power',
      unit: 'W',
      points: [
        { t: 1000, v: 1 },
        { t: 1000, v: 2 },
      ],
    }
    render(<MeterChart series={[doubled]} />)
    expect(screen.getByRole('img').querySelectorAll('circle')).toHaveLength(2)
  })
})
