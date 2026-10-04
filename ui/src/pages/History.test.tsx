import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CommandRecord, HistoryInfo, HistoryTransaction, MessageRecord, StatusRecord } from '../api/client'
import { mockApi, mockFetch, org, renderApp, respond, signedIn } from '../test-utils'

const info = (overrides: Partial<HistoryInfo> = {}): HistoryInfo => ({
  available: true,
  reason: null,
  messages_enabled: true,
  heartbeats_enabled: false,
  retention_days: { messages: 30, commands: 365 },
  counts: {},
  ...overrides,
})

const page = <T,>(items: T[], next: string | null = null, extra: { available?: boolean; reason?: string | null } = {}) => ({
  available: true,
  reason: null,
  next_cursor: next,
  items,
  ...extra,
})

const transaction = (overrides: Partial<HistoryTransaction> = {}): HistoryTransaction => ({
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

const status = (overrides: Partial<StatusRecord> = {}): StatusRecord => ({
  org: 'Fleet',
  charger_id: 'CP-001',
  connector_id: 1,
  status: 'Charging',
  error_code: 'NoError',
  info: null,
  vendor_id: null,
  vendor_error_code: null,
  timestamp: '2026-10-04T10:00:05Z',
  ...overrides,
})

const command = (overrides: Partial<CommandRecord> = {}): CommandRecord => ({
  org: 'Fleet',
  charger_id: 'CP-001',
  message_id: 'm-1',
  action: 'Reset',
  payload: { type: 'Soft' },
  status: 'success',
  response: { status: 'Accepted' },
  error: null,
  sent_at: '2026-10-04T10:00:00Z',
  finished_at: '2026-10-04T10:00:01Z',
  duration_ms: 1000,
  ...overrides,
})

const message = (overrides: Partial<MessageRecord> = {}): MessageRecord => ({
  org: 'Fleet',
  charger_id: 'CP-001',
  direction: 'in',
  type: 'call',
  action: 'Authorize',
  message_id: 'a-1',
  payload: { idTag: 'TAG1' },
  error: null,
  truncated: false,
  size: null,
  timestamp: '2026-10-04T10:00:00Z',
  ...overrides,
})

function broker(routes: Record<string, unknown | (() => unknown)> = {}) {
  signedIn()
  return mockApi({ '/api/orgs': [org()], '/api/history/info': info(), ...routes })
}

const requests = (fetch: ReturnType<typeof mockApi>, path: string) =>
  fetch.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith(path))

describe('the history page', () => {
  it('lists transactions newest first with their charger, energy and how they ended, and links to each', async () => {
    broker({
      '/api/history/transactions': page([
        transaction({ transaction_id: 8, open: true, stopped_at: null, energy_wh: null, meter_stop: null, stop_reason: null, started_at: new Date(Date.now() - 600_000).toISOString() }),
        transaction(),
      ]),
    })
    renderApp('/history')
    const table = within(await screen.findByRole('table', { name: 'Transactions, newest first' }))
    const rows = table.getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    const running = within(rows[0]!)
    expect(running.getByRole('link', { name: '#8' })).toHaveAttribute('href', '/history/transactions/Fleet/CP-001/8')
    expect(running.getByText('running')).toBeInTheDocument()
    expect(running.getByText(/^1\d?m/)).toBeInTheDocument()
    const ended = within(rows[1]!)
    expect(ended.getByRole('link', { name: 'CP-001' })).toHaveAttribute('href', '/chargers/Fleet/CP-001')
    expect(ended.getByText('1600 Wh')).toBeInTheDocument()
    expect(ended.getByText('20m 0s')).toBeInTheDocument()
    expect(ended.getByText('Local')).toBeInTheDocument()
    expect(ended.getByText('TAG1')).toBeInTheDocument()
  })

  it('asks for the filters that were chosen, and starts again when one changes', async () => {
    const fetch = broker({ '/api/history/transactions': page([]) })
    renderApp('/history')
    await screen.findByText(/No transaction matches/)
    expect(requests(fetch, '/api/history/transactions')[0]).toBe('/api/history/transactions?limit=50')

    await userEvent.selectOptions(screen.getByLabelText('Organization'), 'Fleet')
    await userEvent.type(screen.getByLabelText('Charger id'), 'CP-9')
    await userEvent.selectOptions(screen.getByLabelText('State'), 'open')
    await userEvent.type(screen.getByLabelText('Id tag'), 'TAG1')
    await userEvent.selectOptions(screen.getByLabelText('Time range'), '24h')
    await waitFor(() => {
      const last = requests(fetch, '/api/history/transactions').at(-1) ?? ''
      const query = new URL(last, 'http://x').searchParams
      expect(Object.fromEntries(query)).toMatchObject({ org: 'Fleet', charger_id: 'CP-9', state: 'open', id_tag: 'TAG1', limit: '50' })
      expect(Date.now() - Date.parse(query.get('since') ?? '')).toBeGreaterThan(86_300_000)
      expect(Date.now() - Date.parse(query.get('since') ?? '')).toBeLessThan(86_500_000)
    })
  })

  it('keeps the start of a time range fixed while the list is read', async () => {
    const fetch = broker({ '/api/history/transactions': page([transaction()]) })
    renderApp('/history?since=1h')
    await screen.findByRole('table')
    const first = requests(fetch, '/api/history/transactions')[0]
    await new Promise((resolve) => setTimeout(resolve, 1200)) // the page's clock ticks every second
    expect(requests(fetch, '/api/history/transactions')).toEqual([first])
  })

  it('loads the next page with the cursor and adds it below, and stops offering more at the end', async () => {
    const fetch = broker({
      '/api/history/transactions': (): unknown => {
        const url = String(fetch.mock.calls.at(-1)?.[0])
        return url.includes('cursor=c1') ? page([transaction({ transaction_id: 5 })]) : page([transaction({ transaction_id: 6 })], 'c1')
      },
    })
    renderApp('/history')
    await screen.findByRole('link', { name: '#6' })
    await userEvent.click(screen.getByRole('button', { name: 'Load older' }))
    await screen.findByRole('link', { name: '#5' })
    expect(screen.getByRole('link', { name: '#6' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load older' })).not.toBeInTheDocument()
    expect(requests(fetch, '/api/history/transactions').at(-1)).toBe('/api/history/transactions?limit=50&cursor=c1')
  })

  it('keeps what it has and says so when the next page cannot be loaded', async () => {
    broker({
      '/api/history/transactions': (): unknown => (fetchCalls() > 1 ? respond({ detail: 'boom' }, 500) : page([transaction({ transaction_id: 6 })], 'c1')),
    })
    const fetchCalls = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/history/transactions')).length
    renderApp('/history')
    await screen.findByRole('link', { name: '#6' })
    await userEvent.click(screen.getByRole('button', { name: 'Load older' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load more: boom')
    expect(screen.getByRole('link', { name: '#6' })).toBeInTheDocument()
  })

  it('says why there is no history when MongoDB is missing, instead of an empty table', async () => {
    broker({
      '/api/history/info': info({ available: false, reason: 'MongoDB is not configured, so nothing is recorded' }),
      '/api/history/transactions': page([], null, { available: false, reason: 'MongoDB is not configured, so nothing is recorded' }),
    })
    renderApp('/history')
    expect(await screen.findAllByText('MongoDB is not configured, so nothing is recorded')).toHaveLength(2)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows a failure to load as an error', async () => {
    broker({ '/api/history/transactions': respond({ detail: 'boom' }, 500) })
    renderApp('/history')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load: boom')
  })

  it('signs out when the broker stops accepting the key', async () => {
    broker({ '/api/history/info': respond({ detail: 'no' }, 401), '/api/history/transactions': respond({ detail: 'no' }, 401) })
    renderApp('/history')
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
  })

  it('shows status changes, with the error and the connector, on its own tab', async () => {
    const fetch = broker({
      '/api/history/statuses': page([status(), status({ status: 'Faulted', error_code: 'GroundFailure', info: 'earth fault', connector_id: 0 })]),
    })
    renderApp('/history?org=Fleet&charger=CP-001')
    await userEvent.click(await screen.findByRole('button', { name: 'Status changes' }))
    const table = within(await screen.findByRole('table', { name: 'Connector status changes, newest first' }))
    const rows = table.getAllByRole('row').slice(1)
    expect(within(rows[0]!).getByText('Charging')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('Faulted')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('GroundFailure')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('earth fault')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('charger')).toBeInTheDocument()
    const asked = requests(fetch, '/api/history/statuses')[0] ?? ''
    expect(Object.fromEntries(new URL(asked, 'http://x').searchParams)).toEqual({ org: 'Fleet', charger_id: 'CP-001', limit: '50' })
    expect(screen.getByRole('button', { name: 'Status changes' })).toHaveAttribute('aria-current', 'page')
  })

  it('drops the filters of one tab when moving to another and keeps the shared ones', async () => {
    const fetch = broker({ '/api/history/commands': page([]), '/api/history/messages': page([]), '/api/history/transactions': page([]) })
    renderApp('/history?org=Fleet&charger=CP-001&since=7d&state=open&tag=T')
    await screen.findByText(/No transaction matches/)
    await userEvent.click(screen.getByRole('button', { name: 'Commands' }))
    await screen.findByText(/No command matches/)
    const asked = new URL(requests(fetch, '/api/history/commands')[0] ?? '', 'http://x').searchParams
    expect(asked.get('org')).toBe('Fleet')
    expect(asked.get('charger_id')).toBe('CP-001')
    expect(asked.get('since')).not.toBeNull()
    expect(asked.has('state')).toBe(false)
    expect(asked.has('id_tag')).toBe(false)
  })

  it('shows commands with their outcome, duration and what was sent and answered', async () => {
    broker({
      '/api/history/commands': page([
        command(),
        command({ message_id: 'm-2', action: 'ClearCache', status: 'timeout', response: null, error: 'The charger did not answer in 30 s', duration_ms: 30000 }),
      ]),
    })
    renderApp('/history?tab=commands')
    const table = within(await screen.findByRole('table', { name: 'Commands, newest first' }))
    const rows = table.getAllByRole('row').slice(1)
    expect(within(rows[0]!).getByText('success').className).toContain('tone-chip-ok')
    expect(within(rows[0]!).getByText('1000 ms')).toBeInTheDocument()
    await userEvent.click(within(rows[0]!).getByText('Sent'))
    expect(within(rows[0]!).getByText(/"type": "Soft"/)).toBeInTheDocument()
    expect(within(rows[1]!).getByText('timeout').className).toContain('tone-chip-bad')
    expect(within(rows[1]!).getByText('The charger did not answer in 30 s')).toBeInTheDocument()
  })

  it('filters commands by outcome and name', async () => {
    const fetch = broker({ '/api/history/commands': page([]) })
    renderApp('/history?tab=commands')
    await screen.findByText(/No command matches/)
    await userEvent.type(screen.getByLabelText('Command'), 'Reset')
    await userEvent.selectOptions(screen.getByLabelText('Outcome'), 'timeout')
    await waitFor(() => {
      const query = new URL(requests(fetch, '/api/history/commands').at(-1) ?? '', 'http://x').searchParams
      expect(query.get('action')).toBe('Reset')
      expect(query.get('status')).toBe('timeout')
    })
  })

  it('shows messages by direction, labels replies by their action, and says when a payload was too large', async () => {
    broker({
      '/api/history/messages': page([
        message(),
        message({ direction: 'out', type: 'result', message_id: 'a-1', payload: { idTagInfo: { status: 'Accepted' } } }),
        message({ type: 'error', action: null, message_id: 'b', direction: 'in', payload: {}, error: { code: 'NotSupported', description: 'no such thing' } }),
        message({ action: 'DataTransfer', message_id: 'c', payload: null, truncated: true, size: 90000 }),
      ]),
    })
    renderApp('/history?tab=messages')
    const table = within(await screen.findByRole('table', { name: 'OCPP messages, newest first' }))
    const rows = table.getAllByRole('row').slice(1)
    expect(within(rows[0]!).getByText('Authorize')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('from charger')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('Authorize result')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('to charger')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('reply error')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('NotSupported')).toBeInTheDocument()
    expect(within(rows[3]!).getByText(/too large to keep \(90000 bytes\)/)).toBeInTheDocument()
  })

  it('tells the reader the message log is off, how to turn it on, and still shows what is stored', async () => {
    broker({ '/api/history/info': info({ messages_enabled: false }), '/api/history/messages': page([message()]) })
    renderApp('/history?tab=messages')
    expect(await screen.findByText(/The message log is off/)).toHaveTextContent('mongodb.history.messages: true')
    expect(await screen.findByRole('table')).toBeInTheDocument()
  })

  it('does not warn about the message log when it is on', async () => {
    broker({ '/api/history/messages': page([]) })
    renderApp('/history?tab=messages')
    await screen.findByText('No message matches.')
    expect(screen.queryByText(/The message log is off/)).not.toBeInTheDocument()
  })

  it('reads again from the top when Refresh is pressed', async () => {
    const fetch = broker({ '/api/history/transactions': page([transaction()]) })
    renderApp('/history')
    await screen.findByRole('table')
    const before = requests(fetch, '/api/history/transactions').length
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(requests(fetch, '/api/history/transactions').length).toBe(before + 1))
  })

  it('has History in the navigation', async () => {
    broker({ '/api/history/transactions': page([]) })
    renderApp('/history')
    expect(await screen.findByRole('link', { name: 'History' })).toHaveAttribute('href', '/history')
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('counts a time range back from the moment it is chosen, not from when the page opened', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const opened = Date.now()
    const fetch = broker({ '/api/history/transactions': page([]) })
    renderApp('/history')
    await screen.findByText(/No transaction matches/)
    vi.setSystemTime(opened + 2 * 3_600_000)
    await userEvent.selectOptions(screen.getByLabelText('Time range'), '1h')
    await waitFor(() => {
      const last = new URL(requests(fetch, '/api/history/transactions').at(-1) ?? '', 'http://x').searchParams.get('since')
      expect(Math.abs(Date.parse(last ?? '') - (opened + 3_600_000))).toBeLessThan(10_000)
    })
  })

  it('does not carry a filter from one tab to another tab that reads the same name differently', async () => {
    const fetch = broker({ '/api/history/statuses': page([]), '/api/history/commands': page([]), '/api/history/messages': page([]) })
    renderApp('/history?tab=statuses&status=Faulted&action=Reset')
    await screen.findByText('No status change matches.')
    expect(requests(fetch, '/api/history/statuses')[0]).toContain('status=Faulted')
    await userEvent.click(screen.getByRole('button', { name: 'Commands' }))
    await screen.findByText(/No command matches/)
    const asked = new URL(requests(fetch, '/api/history/commands')[0] ?? '', 'http://x').searchParams
    expect(asked.has('status')).toBe(false)
    expect(asked.has('action')).toBe(false)
  })

  it('goes on to the third page and the fourth: each page names the cursor of the next', async () => {
    const fetch = broker({
      '/api/history/transactions': (): unknown => {
        const url = String(vi.mocked(globalThis.fetch).mock.calls.at(-1)?.[0])
        if (url.includes('cursor=c2')) return page([transaction({ transaction_id: 4 })])
        if (url.includes('cursor=c1')) return page([transaction({ transaction_id: 5 })], 'c2')
        return page([transaction({ transaction_id: 6 })], 'c1')
      },
    })
    renderApp('/history')
    await screen.findByRole('link', { name: '#6' })
    await userEvent.click(screen.getByRole('button', { name: 'Load older' }))
    await screen.findByRole('link', { name: '#5' })
    await userEvent.click(screen.getByRole('button', { name: 'Load older' }))
    await screen.findByRole('link', { name: '#4' })
    expect(requests(fetch, '/api/history/transactions').at(-1)).toContain('cursor=c2')
    expect(screen.queryByRole('button', { name: 'Load older' })).not.toBeInTheDocument()
  })

  it('does not add an older page that arrives after the list was changed', async () => {
    let release: (response: Response) => void = () => undefined
    mockFetch((url) => {
      if (url.startsWith('/api/orgs')) return respond([org()])
      if (url.startsWith('/api/history/info')) return respond(info())
      if (url.includes('cursor=c1')) return new Promise<Response>((resolve) => { release = resolve }) as unknown as Response
      if (url.includes('org=Fleet')) return respond(page([transaction({ transaction_id: 99 })]))
      return respond(page([transaction({ transaction_id: 6 })], 'c1'))
    })
    signedIn()
    renderApp('/history')
    await screen.findByRole('link', { name: '#6' })
    await userEvent.click(screen.getByRole('button', { name: 'Load older' }))
    await userEvent.selectOptions(screen.getByLabelText('Organization'), 'Fleet')
    await screen.findByRole('link', { name: '#99' })
    release(respond(page([transaction({ transaction_id: 5 })])))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.queryByRole('link', { name: '#5' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '#99' })).toBeInTheDocument()
  })
})
