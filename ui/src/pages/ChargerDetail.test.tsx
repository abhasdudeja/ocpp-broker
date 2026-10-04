import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ago, backendLink, brokerEvent, detail, mockApi, mockEvents, renderApp, respond, signedIn } from '../test-utils'

const PATH = '/api/chargers/Fleet/CP-001'

async function open(path = '/chargers/Fleet/CP-001') {
  signedIn()
  renderApp(path)
  await screen.findByRole('heading', { name: /CP-001|Home|WB-01/ })
}

describe('charger detail', () => {
  it('shows who the charger is, how it is connected and what it reported', async () => {
    mockApi({ [PATH]: detail() })
    await open()
    expect(await screen.findByText('Fleet · relay mode · OCPP 1.6')).toBeInTheDocument()

    const identity = within(screen.getByRole('region', { name: 'Identity' }))
    expect(identity.getByText('Acme')).toBeInTheDocument()
    expect(identity.getByText('SN-7')).toBeInTheDocument()
    expect(identity.getByText('8931088')).toBeInTheDocument()
    expect(identity.getByText('EM-3')).toBeInTheDocument()

    const connection = within(screen.getByRole('region', { name: 'Connection' }))
    expect(connection.getByText('10.0.0.5:51234')).toBeInTheDocument()
    expect(connection.getByText('12 received, 11 sent')).toBeInTheDocument()

    const connectors = within(screen.getByRole('region', { name: 'Connectors' }))
    expect(connectors.getByText('Connector 1')).toBeInTheDocument()
    expect(connectors.getByText('Charging')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '← Chargers' })).toHaveAttribute('href', '/chargers')
  })

  it('shows the leader and the followers, in that order', async () => {
    mockApi({ [PATH]: detail() })
    await open()
    const backends = within(await screen.findByRole('list', { name: 'Backends' }))
    expect(backends.getAllByRole('listitem').map((li) => li.querySelector('strong')?.textContent)).toEqual(['primary', 'standby'])
  })

  it('shows each backend its own transaction id next to the one the charger holds', async () => {
    mockApi({
      [PATH]: detail({
        open_transactions: 1,
        transactions: [{ transaction_id: 100, state: 'open', backend_ids: { primary: 100, standby: 1 }, degraded: [], awaiting: [] }],
      }),
    })
    await open()
    const table = within(await screen.findByRole('table'))
    expect(table.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Charger holds', 'State', 'primary', 'standby'])
    const row = within(table.getByRole('row', { name: /open/ }))
    expect(row.getAllByRole('cell').map((c) => c.textContent)).toEqual(['100', 'open', '100', '1'])
  })

  it('says which backends are skipped for a transaction and which have not yet said their id', async () => {
    mockApi({
      [PATH]: detail({
        backends: [backendLink(), backendLink({ key: 'a', role: 'follower' }), backendLink({ key: 'b', role: 'follower' })],
        transactions: [{ transaction_id: 7, state: 'open', backend_ids: { primary: 7 }, degraded: ['a'], awaiting: ['b'] }],
      }),
    })
    await open()
    const row = within(await screen.findByRole('row', { name: /open/ }))
    expect(row.getByText('skipped')).toBeInTheDocument()
    expect(row.getByText('waiting for its id')).toBeInTheDocument()
  })

  it('explains that ids are not translated when there is only one backend or the broker answers itself', async () => {
    mockApi({
      [PATH]: detail({
        mode: 'broker',
        transaction_id_mapping: false,
        backends: [backendLink({ key: 'broker', url: null, local: true })],
        followers_total: 0,
      }),
    })
    await open()
    expect(await screen.findByText(/Transaction ids are not translated for this charger/)).toBeInTheDocument()
    expect(screen.getByText('this broker')).toBeInTheDocument()
    expect(screen.getByText('Nothing to show.')).toBeInTheDocument()
  })

  it('says when no transaction has been tracked yet', async () => {
    mockApi({ [PATH]: detail() })
    await open()
    expect(await screen.findByText('No transactions tracked for this charger yet.')).toBeInTheDocument()
  })

  it('does not show a transaction that has no id and no backend has seen', async () => {
    mockApi({ [PATH]: detail({ transactions: [{ transaction_id: null, state: 'pending', backend_ids: {}, degraded: [], awaiting: [] }] }) })
    await open()
    expect(await screen.findByText('No transactions tracked for this charger yet.')).toBeInTheDocument()
  })

  it('lists reservations and charging profiles with each backend\'s own number', async () => {
    mockApi({
      [PATH]: detail({
        reservations: [{ id: 6, backend_ids: { standby: 5 }, expires: null }],
        charging_profiles: [{ id: 3, backend_ids: { primary: 3 }, expires: null }],
      }),
    })
    await open()
    const reservations = within((await screen.findByRole('heading', { name: 'Reservations' })).closest('section')!)
    expect(reservations.getAllByRole('cell').map((c) => c.textContent)).toEqual(['6', '—', '5'])
    const profiles = within(screen.getByRole('heading', { name: 'Charging profiles' }).closest('section')!)
    expect(profiles.getAllByRole('cell').map((c) => c.textContent)).toEqual(['3', '3', '—'])
  })

  it('shows a connector error with its code and text, but not "NoError"', async () => {
    mockApi({
      [PATH]: detail({
        connectors: [
          { connector_id: 1, status: 'Faulted', error_code: 'GroundFailure', info: 'RCD tripped', updated_at: ago(5) },
          { connector_id: 2, status: 'Available', error_code: 'NoError', info: null, updated_at: ago(5) },
          { connector_id: 0, status: 'Available', error_code: null, info: null, updated_at: ago(5) },
        ],
      }),
    })
    await open()
    const connectors = within(await screen.findByRole('region', { name: 'Connectors' }))
    expect(connectors.getByText('GroundFailure')).toBeInTheDocument()
    expect(connectors.getByText(/RCD tripped/)).toBeInTheDocument()
    expect(connectors.queryByText('NoError')).not.toBeInTheDocument()
    expect(connectors.getByText('Charger as a whole')).toBeInTheDocument()
  })

  it('waits for the boot message and for a first connector status', async () => {
    mockApi({ [PATH]: detail({ boot: null, last_heartbeat_at: null, connectors: [] }) })
    await open()
    expect(await screen.findByText("Waiting for the charger's BootNotification.")).toBeInTheDocument()
    expect(screen.getByText('The charger has not reported a connector status yet.')).toBeInTheDocument()
  })

  it('lists what the transaction id table has done, collapsed', async () => {
    mockApi({ [PATH]: detail({ id_table_stats: { rewritten: 4, skipped: 1 } }) })
    await open()
    const details = (await screen.findByText('What the transaction id table has done')).closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(within(details).getByText('rewritten').nextElementSibling).toHaveTextContent('4')
    expect(within(details).getByText('skipped').nextElementSibling).toHaveTextContent('1')
  })

  it('leaves the id table section out when it has done nothing', async () => {
    mockApi({ [PATH]: detail({ id_table_stats: {} }) })
    await open()
    await screen.findByText('Fleet · relay mode · OCPP 1.6')
    expect(screen.queryByText('What the transaction id table has done')).not.toBeInTheDocument()
  })

  it('asks for the charger by its encoded id', async () => {
    const fetch = mockApi({ '/api/chargers/': detail({ charger_id: 'a/b' }) })
    signedIn()
    renderApp('/chargers/My%20Org/a%2Fb')
    expect(await screen.findByRole('heading', { name: 'a/b' })).toBeInTheDocument()
    await screen.findByText(/relay mode/)
    expect(fetch.mock.calls.map(([url]) => String(url))).toContain('/api/chargers/My%20Org/a%2Fb')
  })

  it('says when the charger is not connected, and notices when it comes back', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let present = false
    mockApi({ [PATH]: () => (present ? detail() : respond({ detail: 'Charger Fleet/CP-001 is not connected to this instance' }, 404)) })
    await open()
    expect(await screen.findByRole('alert')).toHaveTextContent('This charger is not connected to this broker instance.')

    present = true
    await act(() => vi.advanceTimersByTimeAsync(3_000))
    expect(await screen.findByText('Fleet · relay mode · OCPP 1.6')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps what it knew, and says so, when the charger disconnects', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let present = true
    mockApi({ [PATH]: () => (present ? detail() : respond({ detail: 'gone' }, 404)) })
    await open()
    await screen.findByText('Fleet · relay mode · OCPP 1.6')

    present = false
    await act(() => vi.advanceTimersByTimeAsync(3_000))
    expect(await screen.findByRole('alert')).toHaveTextContent('not connected to this broker instance any more')
    expect(screen.getByText('SN-7')).toBeInTheDocument()
  })

  it('reports another failure as an error, not as a disconnect', async () => {
    mockApi({ [PATH]: respond({ detail: 'boom' }, 500) })
    await open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load: boom')
  })

  it('signs out when the broker stops accepting the key', async () => {
    mockApi({ [PATH]: respond({ detail: 'no' }, 401) })
    signedIn()
    renderApp('/chargers/Fleet/CP-001')
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
  })

  describe('changing the leader', () => {
    it('offers a connected follower and reads the charger again once it leads', async () => {
      let leader = 'primary'
      const fetch = mockApi({
        [PATH]: () =>
          detail({
            backends:
              leader === 'primary'
                ? [backendLink(), backendLink({ key: 'standby', url: 'ws://standby.example.com/ocpp', role: 'follower' })]
                : [backendLink({ key: 'standby', url: 'ws://standby.example.com/ocpp', configured_leader: false }), backendLink({ key: 'primary', role: 'follower', configured_leader: true })],
          }),
        [PATH + '/leader']: () => {
          leader = 'standby'
          return { old_leader: 'primary', new_leader: 'standby' }
        },
      })
      await open()
      await userEvent.click(await screen.findByRole('button', { name: 'Make standby the leader' }))
      await userEvent.click(within(screen.getByRole('group', { name: 'Confirm standby' })).getByRole('button', { name: 'Make standby the leader' }))
      expect(await screen.findByText('standby leads now.')).toBeInTheDocument()
      expect(await screen.findByText('not the configured leader')).toBeInTheDocument()
      expect(fetch.mock.calls.some(([url, init]) => String(url) === PATH + '/leader' && init?.method === 'POST')).toBe(true)
    })

    it('offers nothing when no follower is connected', async () => {
      mockApi({ [PATH]: detail({ backends: [backendLink()] }) })
      await open()
      await screen.findByRole('region', { name: 'Connectors' })
      expect(screen.queryByRole('heading', { name: 'Change the leader' })).not.toBeInTheDocument()
    })
  })

  describe('status history', () => {
    const changes = (items: unknown[], available = true) => ({ available, reason: null, next_cursor: null, items })
    const change = (status: string, overrides: Record<string, unknown> = {}) => ({
      org: 'Fleet',
      charger_id: 'CP-001',
      connector_id: 1,
      status,
      error_code: 'NoError',
      info: null,
      vendor_id: null,
      vendor_error_code: null,
      timestamp: '2026-10-04T10:00:00Z',
      ...overrides,
    })

    it('lists the latest status changes with a link to all of them', async () => {
      const fetch = mockApi({
        [PATH]: detail(),
        '/api/history/statuses': changes([change('Charging'), change('Faulted', { error_code: 'GroundFailure', connector_id: 0 })]),
      })
      await open()
      const section = within(await screen.findByRole('region', { name: 'Recent status changes' }))
      const items = section.getAllByRole('listitem')
      expect(items).toHaveLength(2)
      expect(within(items[0]!).getByText('Charging')).toBeInTheDocument()
      expect(within(items[1]!).getByText('Faulted')).toBeInTheDocument()
      expect(within(items[1]!).getByText('GroundFailure')).toBeInTheDocument()
      expect(within(items[1]!).getByText('charger')).toBeInTheDocument()
      expect(section.getByRole('link', { name: 'All status changes of this charger' })).toHaveAttribute('href', '/history?tab=statuses&org=Fleet&charger=CP-001')
      const asked = new URL(String(fetch.mock.calls.find(([url]) => String(url).startsWith('/api/history/statuses'))?.[0]), 'http://x').searchParams
      expect(Object.fromEntries(asked)).toEqual({ org: 'Fleet', charger_id: 'CP-001', limit: '10' })
    })

    it('says when none has been recorded', async () => {
      mockApi({ [PATH]: detail(), '/api/history/statuses': changes([]) })
      await open()
      expect(await screen.findByText('No status change has been recorded for this charger.')).toBeInTheDocument()
    })

    it('is left out when there is no history, or it cannot be read', async () => {
      const fetch = mockApi({ [PATH]: detail(), '/api/history/statuses': changes([], false) })
      await open()
      await screen.findByRole('region', { name: 'Connectors' })
      await vi.waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).startsWith('/api/history/statuses'))).toBe(true))
      await act(() => new Promise((resolve) => setTimeout(resolve, 50))) // the answer has been read
      expect(screen.queryByRole('region', { name: 'Recent status changes' })).not.toBeInTheDocument()
    })

    it('reads again when this charger changes status, and only then', async () => {
      const events = mockEvents()
      let items = [change('Charging')]
      const fetch = mockApi({ [PATH]: detail(), '/api/history/statuses': () => changes(items) }, events.handler)
      await open()
      await screen.findByRole('region', { name: 'Recent status changes' })
      await vi.waitFor(() => expect(events.streams.length).toBeGreaterThan(0))
      act(() => events.latest()?.open())
      await screen.findByText('Live')
      const reads = () => fetch.mock.calls.filter(([url]) => String(url).startsWith('/api/history/statuses')).length
      const before = reads()

      act(() => events.latest()?.emit(brokerEvent('charger.status', { connector_id: 1, status: 'Charging' }, { charger_id: 'CP-999' })))
      await act(() => new Promise((resolve) => setTimeout(resolve, 1900)))
      expect(reads()).toBe(before)

      items = [change('Faulted'), change('Charging')]
      act(() => events.latest()?.emit(brokerEvent('charger.status', { connector_id: 1, status: 'Faulted' })))
      await act(() => new Promise((resolve) => setTimeout(resolve, 1900)))
      expect(reads()).toBe(before + 1)
      expect(await screen.findByText('Faulted')).toBeInTheDocument()
    })

    it('is left out when the request fails, and the rest of the page is unaffected', async () => {
      mockApi({ [PATH]: detail(), '/api/history/statuses': respond({ detail: 'boom' }, 500) })
      await open()
      await screen.findByRole('region', { name: 'Connectors' })
      expect(screen.queryByRole('region', { name: 'Recent status changes' })).not.toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })
  })
})
