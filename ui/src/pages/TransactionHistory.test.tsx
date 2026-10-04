import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { HistoryTransaction, MeterReading, TransactionDetail } from '../api/client'
import { mockApi, renderApp, respond, signedIn } from '../test-utils'

const PATH = '/api/history/transactions/Fleet/CP-001/7'

const tx = (overrides: Partial<HistoryTransaction> = {}): HistoryTransaction => ({
  org: 'Fleet',
  charger_id: 'CP-001',
  transaction_id: 7,
  connector_id: 1,
  id_tag: 'TAG1',
  started_at: '2026-10-04T10:00:00Z',
  stopped_at: '2026-10-04T10:20:00Z',
  meter_start: 1000,
  meter_stop: 2600,
  energy_wh: 1600,
  stop_reason: 'Local',
  stop_id_tag: 'TAG1',
  open: false,
  ...overrides,
})

const reading = (overrides: Partial<MeterReading> = {}): MeterReading => ({
  timestamp: '2026-10-04T10:05:00Z',
  connector_id: 1,
  transaction_id: 7,
  measurand: 'Energy.Active.Import.Register',
  phase: null,
  unit: 'Wh',
  context: null,
  location: null,
  value: 1500,
  raw_value: '1500',
  ...overrides,
})

const detail = (overrides: Partial<TransactionDetail> = {}): TransactionDetail => ({
  available: true,
  reason: null,
  transaction: tx(),
  readings: [reading(), reading({ timestamp: '2026-10-04T10:10:00Z', value: 2100 })],
  readings_truncated: false,
  ...overrides,
})

async function open(body: unknown) {
  signedIn()
  mockApi({ [PATH]: body })
  renderApp('/history/transactions/Fleet/CP-001/7')
  await screen.findByRole('heading', { name: /Transaction #7/ })
}

describe('a transaction in the history', () => {
  it('shows what the transaction was: who, when, how long and how much', async () => {
    await open(detail())
    expect(await screen.findByText('TAG1')).toBeInTheDocument()
    expect(screen.getByText('20m 0s')).toBeInTheDocument()
    expect(screen.getByText('1600 Wh')).toBeInTheDocument()
    expect(screen.getByText('1000 Wh')).toBeInTheDocument()
    expect(screen.getByText('2600 Wh')).toBeInTheDocument()
    expect(screen.getByText('Local')).toBeInTheDocument()
    expect(screen.getByText('ended')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'CP-001' })).toHaveAttribute('href', '/chargers/Fleet/CP-001')
    expect(within(screen.getByRole('main')).getByRole('link', { name: 'History' })).toHaveAttribute('href', '/history')
  })

  it('draws the meter readings', async () => {
    await open(detail())
    const picture = await screen.findByRole('img')
    expect(picture.getAttribute('aria-label')).toMatch(/Energy\.Active\.Import\.Register, 2 readings .* from 1500 to 2100 Wh/)
  })

  it('marks a transaction that is still running, with what is not known yet left empty', async () => {
    await open(detail({ transaction: tx({ open: true, stopped_at: null, meter_stop: null, energy_wh: null, stop_reason: null, started_at: new Date(Date.now() - 3_600_000).toISOString() }) }))
    expect(await screen.findByText('running')).toBeInTheDocument()
    expect(screen.queryByText('ended')).not.toBeInTheDocument()
    expect(screen.getByText(/^1h/)).toBeInTheDocument()
  })

  it('names a different tag the transaction was stopped with, and does not repeat the same one', async () => {
    await open(detail({ transaction: tx({ stop_id_tag: 'ADMIN' }) }))
    expect(await screen.findByText('ADMIN')).toBeInTheDocument()
  })

  it('says when no reading could be drawn', async () => {
    await open(detail({ readings: [reading({ value: null, raw_value: 'n/a' })] }))
    expect(await screen.findByText(/No numeric meter readings/)).toBeInTheDocument()
  })

  it('warns that some readings are not shown', async () => {
    await open(detail({ readings_truncated: true }))
    expect(await screen.findByText(/more readings than are shown/)).toBeInTheDocument()
  })

  it('says a transaction is not there, and why that can be', async () => {
    signedIn()
    mockApi({ [PATH]: respond({ detail: 'Transaction not found' }, 404) })
    renderApp('/history/transactions/Fleet/CP-001/7')
    expect(await screen.findByText(/not in the history/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says why history is not available', async () => {
    await open(detail({ available: false, reason: 'MongoDB is not connected', transaction: null, readings: [] }))
    expect(await screen.findByText('MongoDB is not connected')).toBeInTheDocument()
  })

  it('shows another failure as an error', async () => {
    signedIn()
    mockApi({ [PATH]: respond({ detail: 'boom' }, 500) })
    renderApp('/history/transactions/Fleet/CP-001/7')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load: boom')
  })

  it('asks for the names in the address as they are, encoded', async () => {
    signedIn()
    const fetch = mockApi({ '/api/history/transactions/': detail() })
    renderApp('/history/transactions/My%20Org/CP%2F1/7')
    await screen.findByRole('heading', { name: /Transaction #7/ })
    expect(String(fetch.mock.calls.find(([url]) => String(url).includes('/api/history/transactions/'))?.[0])).toBe('/api/history/transactions/My%20Org/CP%2F1/7')
  })
})
