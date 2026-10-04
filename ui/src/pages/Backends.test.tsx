import { act, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { BackendStat, OrgBackends } from '../api/client'
import { brokerEvent, mockApi, mockEvents, renderApp, respond, signedIn } from '../test-utils'

const stat = (overrides: Partial<BackendStat> = {}): BackendStat => ({
  key: 'primary',
  url: 'ws://primary.example.com/ocpp',
  local: false,
  configured_leader: true,
  leading: 3,
  following: 0,
  links_up: 3,
  links_down: 0,
  buffered_frames: 0,
  down_chargers: [],
  ...overrides,
})

const standby = (overrides: Partial<BackendStat> = {}) =>
  stat({ key: 'standby', url: 'ws://standby.example.com/ocpp', configured_leader: false, leading: 0, following: 3, ...overrides })

const fleet = (backends: BackendStat[] = [stat(), standby()], chargers = 3): OrgBackends => ({ org: 'Fleet', mode: 'relay', chargers, backends })

async function open(data: unknown) {
  signedIn()
  mockApi({ '/api/backends': data })
  renderApp('/backends')
  await screen.findByRole('heading', { name: 'Backends' })
}

describe('the backends page', () => {
  it('shows each backend with its role, how many chargers it leads and follows, and its links', async () => {
    await open([fleet()])
    const table = within(await screen.findByRole('table', { name: 'Backends of Fleet' }))
    const primary = within(table.getByRole('row', { name: /primary/ }))
    expect(primary.getByText('ws://primary.example.com/ocpp')).toBeInTheDocument()
    expect(primary.getByText('3 up')).toBeInTheDocument()
    expect(primary.queryByText(/down/)).not.toBeInTheDocument()
    expect(primary.getAllByRole('cell').slice(0, 3).map((c) => c.textContent)).toEqual(['leader', '3', '0'])
    const second = within(table.getByRole('row', { name: /standby/ }))
    expect(second.getAllByRole('cell').slice(0, 3).map((c) => c.textContent)).toEqual(['follower', '0', '3'])
    expect(screen.getByRole('heading', { name: /Fleet/ })).toHaveTextContent('3 chargers connected')
  })

  it('shows a backend that is down, what waits for it, and links to the chargers that lost it', async () => {
    await open([fleet([stat({ links_up: 1, links_down: 2, buffered_frames: 4, down_chargers: ['CP1', 'CP2'] }), standby()])])
    const row = within(await screen.findByRole('row', { name: /primary/ }))
    expect(row.getByText('2 down').className).toContain('tone-chip-bad')
    expect(row.getByText('4 waiting')).toBeInTheDocument()
    expect(row.getByRole('link', { name: 'CP1' })).toHaveAttribute('href', '/chargers/Fleet/CP1')
    expect(row.getByRole('link', { name: 'CP2' })).toHaveAttribute('href', '/chargers/Fleet/CP2')
  })

  it('says how many more chargers lost a backend than are named', async () => {
    const named = Array.from({ length: 20 }, (_, n) => `CP${n}`)
    await open([fleet([stat({ links_up: 0, links_down: 27, down_chargers: named })])])
    expect(await screen.findByText('and 7 more')).toBeInTheDocument()
  })

  it('marks the broker itself as a backend, and has no address for it', async () => {
    await open([
      {
        org: 'Hybrid',
        mode: 'broker',
        chargers: 1,
        backends: [stat({ key: 'broker', url: null, local: true, leading: 1, following: 0, links_up: 1 }), standby({ key: 'mirror', following: 1, links_up: 1 })],
      },
    ])
    const row = within(await screen.findByRole('row', { name: /broker/ }))
    expect(row.getByText('this broker')).toBeInTheDocument()
    expect(row.queryByText(/ws:\/\//)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Hybrid/ })).toHaveTextContent('the broker answers, the others receive copies')
  })

  it('explains an installation with no backends', async () => {
    await open([])
    expect(await screen.findByText(/No organization has backends/)).toBeInTheDocument()
  })

  it('reports a failed refresh and keeps what it had', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let broken = false
    signedIn()
    mockApi({ '/api/backends': () => (broken ? respond({ detail: 'boom' }, 500) : [fleet()]) })
    renderApp('/backends')
    await screen.findByRole('row', { name: /primary/ })
    broken = true
    await act(() => vi.advanceTimersByTimeAsync(5_000))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not refresh: boom')
    expect(screen.getByRole('row', { name: /primary/ })).toBeInTheDocument()
  })

  it('signs out when the broker stops accepting the key', async () => {
    signedIn()
    mockApi({ '/api/backends': respond({ detail: 'no' }, 401) })
    renderApp('/backends')
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
  })

  it('is not thrown by an answer that is not a list', async () => {
    await open({ detail: 'odd' })
    expect(await screen.findByText(/No organization has backends/)).toBeInTheDocument()
  })

  it('reads again at once when a backend link changes, and lists backend events only', async () => {
    const events = mockEvents()
    let backends = [fleet()]
    const fetch = mockApi({ '/api/backends': () => backends }, events.handler)
    signedIn()
    renderApp('/backends')
    await screen.findByRole('row', { name: /primary/ })
    await vi.waitFor(() => expect(events.streams.length).toBeGreaterThan(0))
    act(() => events.latest()?.open())
    await screen.findByText('Live')
    const calls = () => fetch.mock.calls.filter(([url]) => String(url) === '/api/backends').length
    const before = calls()

    backends = [fleet([stat({ links_up: 2, links_down: 1, down_chargers: ['CP9'] }), standby()])]
    act(() => {
      events.latest()?.emit(brokerEvent('backend.link', { backend: 'primary', role: 'leader', connected: false }, { charger_id: 'CP9' }))
      events.latest()?.emit(brokerEvent('charger.status', { connector_id: 1, status: 'Charging', previous: null }))
    })
    expect(await screen.findByText('1 down')).toBeInTheDocument()
    expect(calls()).toBe(before + 1)
    const feed = within(screen.getByRole('region', { name: 'Recent backend events' }))
    expect(feed.getAllByRole('listitem')).toHaveLength(1)
    expect(feed.getByText('primary (leader) was lost')).toBeInTheDocument()
  })
})
