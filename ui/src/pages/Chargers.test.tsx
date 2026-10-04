import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ago, backendLink, charger, info, mockApi, org, renderApp, respond, signedIn } from '../test-utils'

const list = (...chargers: ReturnType<typeof charger>[]) => ({ chargers, total: chargers.length })

async function open(path = '/chargers') {
  signedIn()
  renderApp(path)
  await screen.findByRole('heading', { name: 'Chargers' })
}

describe('chargers list', () => {
  it('shows each connected charger with its identity, leader, followers, connectors and transactions', async () => {
    mockApi({
      '/api/orgs': [org()],
      '/api/chargers': list(
        charger({ open_transactions: 2, degraded_transactions: 1, connector_statuses: { '1': 'Charging', '0': 'Available', '2': 'Faulted' } }),
      ),
    })
    await open()
    const row = within(await screen.findByRole('row', { name: /CP-001/ }))
    expect(row.getByRole('link', { name: 'CP-001' })).toHaveAttribute('href', '/chargers/Fleet/CP-001')
    expect(row.getByText('Acme Wallbox 7')).toBeInTheDocument()
    expect(row.getByText('Fleet')).toBeInTheDocument()
    expect(row.getByText('OCPP 1.6')).toBeInTheDocument()
    expect(row.getByText('primary')).toBeInTheDocument()
    expect(row.getByText('1/1 connected')).toBeInTheDocument()
    expect(row.getByText('2')).toBeInTheDocument()
    expect(row.getByText('1 with a backend skipped')).toBeInTheDocument()
    const statuses = row.getAllByRole('listitem').map((li) => li.textContent)
    expect(statuses).toEqual(['charger Available', '#1 Charging', '#2 Faulted'])
    expect(row.getByText(/\d+s ago/)).toBeInTheDocument()
  })

  it('colours what needs attention: a faulted connector, followers that are not all connected', async () => {
    mockApi({
      '/api/orgs': [org()],
      '/api/chargers': list(
        charger({
          connector_statuses: { '1': 'Faulted', '2': 'Available', '3': 'Charging', '4': 'Unavailable', '5': 'Preparing' },
          followers_connected: 1,
          followers_total: 2,
        }),
        charger({ charger_id: 'CP-002', followers_connected: 2, followers_total: 2 }),
      ),
    })
    await open()
    const row = within(await screen.findByRole('row', { name: /CP-001/ }))
    const tone = (text: string) => row.getByText(text).className
    expect(tone('Faulted')).toContain('tone-chip-bad')
    expect(tone('Available')).toContain('tone-chip-ok')
    expect(tone('Charging')).toContain('tone-chip-info')
    expect(tone('Unavailable')).toContain('tone-chip-warn')
    expect(tone('Preparing')).toContain('tone-chip-neutral')
    expect(tone('1/2 connected')).toContain('tone-chip-warn')
    expect(within(screen.getByRole('row', { name: /CP-002/ })).getByText('2/2 connected').className).toContain('tone-chip-ok')
  })

  it('says when a leader is down and when frames are waiting for it', async () => {
    mockApi({
      '/api/orgs': [org()],
      '/api/chargers': list(
        charger({ leader: backendLink({ connected: false }), buffered_frames: 3, followers_connected: 0, followers_total: 2 }),
      ),
    })
    await open()
    const row = within(await screen.findByRole('row', { name: /CP-001/ }))
    expect(row.getByText('Not connected:')).toBeInTheDocument()
    expect(row.getByText('3 waiting')).toBeInTheDocument()
    expect(row.getByText('0/2 connected')).toBeInTheDocument()
  })

  it('shows the broker itself as the leader in broker mode, with no followers', async () => {
    mockApi({
      '/api/orgs': [org()],
      '/api/chargers': list(
        charger({
          org: 'Home', charger_id: 'WB-01', mode: 'broker',
          leader: backendLink({ key: 'broker', url: null, local: true }), followers_total: 0, followers_connected: 0,
        }),
      ),
    })
    await open()
    const row = within(await screen.findByRole('row', { name: /WB-01/ }))
    expect(row.getByText(/this broker/)).toBeInTheDocument()
    expect(row.getByText('none')).toBeInTheDocument()
  })

  it('says so when a charger has not reported its connectors or identity', async () => {
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(charger({ connector_statuses: {}, vendor: null, model: null })) })
    await open()
    const row = within(await screen.findByRole('row', { name: /CP-001/ }))
    expect(row.getByText('not reported')).toBeInTheDocument()
    expect(row.queryByText('Acme Wallbox 7')).not.toBeInTheDocument()
  })

  it('links with the charger id encoded, so an id containing a slash cannot break the address', async () => {
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(charger({ charger_id: 'a/b c', org: 'My Org' })) })
    await open()
    expect(await screen.findByRole('link', { name: 'a/b c' })).toHaveAttribute('href', '/chargers/My%20Org/a%2Fb%20c')
  })

  it('explains an empty list, differently when a filter is set', async () => {
    mockApi({ '/api/orgs': [org()], '/api/chargers': list() })
    await open()
    expect(await screen.findByText(/No chargers are connected to this broker instance right now/)).toBeInTheDocument()
    expect(screen.getByText(/Sessions are per process/)).toBeInTheDocument()
  })

  it('says no charger matches when it is filtered', async () => {
    mockApi({ '/api/orgs': [org()], '/api/chargers': list() })
    await open('/chargers?q=zzz')
    expect(await screen.findByText('No connected charger matches.')).toBeInTheDocument()
  })

  it('asks the broker to filter, and keeps the filter in the address', async () => {
    const fetch = mockApi({ '/api/orgs': [org(), org({ name: 'Home', mode: 'broker', backends: [] })], '/api/chargers': list(charger()) })
    await open('/chargers?org=Fleet&q=cp')
    await screen.findByRole('row', { name: /CP-001/ })
    const urls = () => fetch.mock.calls.map(([url]) => String(url))
    expect(urls()).toContain('/api/chargers?org=Fleet&q=cp')
    expect(screen.getByLabelText('Organization')).toHaveValue('Fleet')
    expect(screen.getByLabelText('Search chargers')).toHaveValue('cp')

    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Organization'), '')
    await user.clear(screen.getByLabelText('Search chargers'))
    await user.type(screen.getByLabelText('Search chargers'), 'wb')
    await vi.waitFor(() => expect(urls()).toContain('/api/chargers?q=wb'))
  })

  it('offers every organization the broker knows in the filter', async () => {
    mockApi({ '/api/orgs': [org(), org({ name: 'Home' })], '/api/chargers': list(charger()) })
    await open()
    await screen.findByRole('row', { name: /CP-001/ })
    const options = within(screen.getByLabelText('Organization')).getAllByRole('option').map((o) => o.textContent)
    expect(options).toEqual(['All organizations', 'Fleet', 'Home'])
  })

  it('refreshes by itself', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let chargers = [charger()]
    mockApi({ '/api/orgs': [org()], '/api/chargers': () => list(...chargers) })
    await open()
    await screen.findByRole('row', { name: /CP-001/ })
    expect(screen.queryByRole('link', { name: 'CP-002' })).not.toBeInTheDocument()
    chargers = [charger(), charger({ charger_id: 'CP-002' })]
    await act(() => vi.advanceTimersByTimeAsync(5_000))
    expect(await screen.findByRole('link', { name: 'CP-002' })).toBeInTheDocument()
    expect(screen.getByText('2 chargers')).toBeInTheDocument()
  })

  it('reports a failed first load, and a failed refresh without losing the list', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let broken = true
    mockApi({
      '/api/orgs': [org()],
      '/api/chargers': () => (broken ? respond({ detail: 'database on fire' }, 500) : list(charger())),
    })
    await open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load: database on fire')

    broken = false
    await act(() => vi.advanceTimersByTimeAsync(5_000))
    await screen.findByRole('row', { name: /CP-001/ })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    broken = true
    await act(() => vi.advanceTimersByTimeAsync(5_000))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not refresh: database on fire')
    expect(screen.getByRole('link', { name: 'CP-001' })).toBeInTheDocument()
  })

  it('keeps the console usable if one page cannot render what it was sent', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mockApi({
      '/api/orgs': [org()],
      '/api/chargers': { chargers: 'not a list', total: 1 },
      '/api/system/info': info(),
    })
    await open()
    expect(await screen.findByText('This page could not be shown.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Overview' }), 'the navigation is still there').toBeInTheDocument()

    await userEvent.setup().click(screen.getByRole('link', { name: 'Overview' }))
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.queryByText('This page could not be shown.')).not.toBeInTheDocument()
  })

  it('lists chargers that are known but not connected now, most recently seen first, when MongoDB remembers them', async () => {
    mockApi({
      '/api/orgs': [org()],
      '/api/chargers': list(charger()),
      '/api/chargers/offline': {
        available: true,
        reason: null,
        total: 2,
        chargers: [
          { org: 'Fleet', charger_id: 'CP-OLD', mode: 'relay', vendor: 'Acme', model: 'Wallbox 7', firmware_version: null, remote_address: null, last_connected_at: ago(700), last_disconnected_at: ago(600), last_seen_at: ago(620), last_boot_at: ago(86_400) },
          { org: 'Home', charger_id: 'WB-GONE', mode: 'broker', vendor: null, model: null, firmware_version: null, remote_address: null, last_connected_at: null, last_disconnected_at: null, last_seen_at: null, last_boot_at: null },
        ],
      },
    })
    await open()
    const section = within(await screen.findByRole('region', { name: 'Not connected now' }))
    const old = within(section.getByRole('row', { name: /CP-OLD/ }))
    expect(old.getByText('Acme Wallbox 7')).toBeInTheDocument()
    expect(old.getByText('relay')).toBeInTheDocument()
    expect(old.getByText('10m ago')).toBeInTheDocument()
    expect(old.getByText('1d ago')).toBeInTheDocument()
    const gone = within(section.getByRole('row', { name: /WB-GONE/ }))
    expect(gone.getByText('never')).toBeInTheDocument()
    expect(section.getByText(/also appears here/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'CP-001' }), 'the connected charger is in the main list').toBeInTheDocument()
  })

  it('says how many known chargers are not shown when there are more than it lists', async () => {
    const one = { org: 'Fleet', charger_id: 'CP-X', mode: null, vendor: null, model: null, firmware_version: null, remote_address: null, last_connected_at: null, last_disconnected_at: null, last_seen_at: ago(5), last_boot_at: null }
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(charger()), '/api/chargers/offline': { available: true, reason: null, total: 900, chargers: [one] } })
    await open()
    expect(await screen.findByText(/the 1 most recent of 900/)).toBeInTheDocument()
  })

  it('says why absent chargers are not listed when MongoDB does not remember them', async () => {
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(charger()) })
    await open()
    expect(await screen.findByText(/Chargers that have gone are not listed: MongoDB is not configured/)).toBeInTheDocument()
  })

  it('says no known charger is missing when every one is connected', async () => {
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(charger()), '/api/chargers/offline': { available: true, reason: null, total: 0, chargers: [] } })
    await open()
    expect(await screen.findByText('No known charger is missing.')).toBeInTheDocument()
  })

  it('asks for the absent chargers with the same filters', async () => {
    const fetch = mockApi({ '/api/orgs': [org()], '/api/chargers': list(charger()) })
    await open('/chargers?org=Fleet&q=cp')
    await screen.findByRole('row', { name: /CP-001/ })
    expect(fetch.mock.calls.map(([url]) => String(url))).toContain('/api/chargers/offline?org=Fleet&q=cp')
  })

  it('shows nothing about absent chargers from an answer it cannot read', async () => {
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(charger()), '/api/chargers/offline': { odd: true } })
    await open()
    await screen.findByRole('row', { name: /CP-001/ })
    expect(screen.queryByRole('region', { name: 'Not connected now' })).not.toBeInTheDocument()
  })

  it('signs out when the broker stops accepting the key', async () => {
    mockApi({ '/api/orgs': respond({ detail: 'no' }, 401), '/api/chargers': respond({ detail: 'no' }, 401) })
    signedIn()
    renderApp('/chargers')
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
    expect(screen.getByText('The broker no longer accepts this key. Sign in again.')).toBeInTheDocument()
  })

  it('keeps counting how long ago the last message was between refreshes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(charger({ last_seen: ago(10) })) })
    await open()
    await screen.findByRole('row', { name: /CP-001/ })
    expect(screen.getByText('10s ago')).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(3_000))
    expect(screen.getByText(/1[3-4]s ago/)).toBeInTheDocument()
  })
})

describe('a long list', () => {
  const many = (count: number) => Array.from({ length: count }, (_, n) => charger({ charger_id: `CP-${String(n).padStart(3, '0')}` }))

  it('shows a hundred chargers and reveals more on request', async () => {
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(...many(250)) })
    await open()
    await screen.findByRole('link', { name: 'CP-000' })
    const rows = () => within(screen.getAllByRole('table')[0]!).getAllByRole('row').length - 1
    expect(rows()).toBe(100)
    expect(screen.getByText('250 chargers')).toBeInTheDocument()
    expect(screen.getByText('150 not shown')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show 100 more chargers' }))
    expect(rows()).toBe(200)
    await userEvent.click(screen.getByRole('button', { name: 'Show 50 more chargers' }))
    expect(rows()).toBe(250)
    expect(screen.queryByRole('button', { name: /more chargers/ })).not.toBeInTheDocument()
  })

  it('goes back to a hundred when the search changes', async () => {
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(...many(250)) })
    await open()
    await screen.findByRole('link', { name: 'CP-000' })
    await userEvent.click(screen.getByRole('button', { name: 'Show 100 more chargers' }))
    await userEvent.type(screen.getByLabelText('Search chargers'), 'CP')
    await vi.waitFor(() => expect(within(screen.getAllByRole('table')[0]!).getAllByRole('row').length - 1).toBe(100))
  })

  it('does the same for the chargers that are not connected', async () => {
    const gone = Array.from({ length: 150 }, (_, n) => ({ org: 'Fleet', charger_id: `OLD-${String(n).padStart(3, '0')}`, mode: 'relay', vendor: null, model: null, firmware_version: null, remote_address: null, last_connected_at: null, last_disconnected_at: null, last_seen_at: ago(60), last_boot_at: null }))
    mockApi({ '/api/orgs': [org()], '/api/chargers': list(charger()), '/api/chargers/offline': { available: true, reason: null, chargers: gone, total: 150 } })
    await open()
    await screen.findByRole('heading', { name: 'Not connected now' })
    expect(screen.getAllByRole('table')[1]!.querySelectorAll('tbody tr')).toHaveLength(100)
    await userEvent.click(screen.getByRole('button', { name: 'Show 50 more chargers' }))
    expect(screen.getAllByRole('table')[1]!.querySelectorAll('tbody tr')).toHaveLength(150)
    await userEvent.type(screen.getByLabelText('Search chargers'), 'OLD')
    await vi.waitFor(() => expect(screen.getAllByRole('table')[1]!.querySelectorAll('tbody tr')).toHaveLength(100))
  })
})
