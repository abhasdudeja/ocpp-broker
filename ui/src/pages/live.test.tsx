import { act, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { brokerEvent, charger, detail, info, mockApi, mockEvents, org, renderApp, signedIn } from '../test-utils'

const PATH = '/api/chargers/Fleet/CP-001'
const list = (...chargers: ReturnType<typeof charger>[]) => ({ chargers, total: chargers.length })
const count = (fetch: ReturnType<typeof mockApi>, prefix: string) => fetch.mock.calls.filter(([url]) => String(url).startsWith(prefix)).length
/** Reads of the list of connected chargers (not the list of absent ones, which lives under the same path) */
const listCalls = (fetch: ReturnType<typeof mockApi>) => fetch.mock.calls.filter(([url]) => /^\/api\/chargers(\?|$)/.test(String(url))).length
const wait = (ms: number) => act(() => new Promise((resolve) => setTimeout(resolve, ms)))

async function live(path: string, routes: Record<string, unknown>) {
  const events = mockEvents()
  const fetch = mockApi(routes, events.handler)
  signedIn()
  renderApp(path)
  await vi.waitFor(() => expect(events.streams.length).toBeGreaterThan(0))
  act(() => events.latest()?.open())
  await screen.findByText('Live')
  return { events, fetch }
}

describe('the live indicator', () => {
  it('says the console is polling when the broker has no event stream', async () => {
    mockApi({ '/api/system/info': info(), '/api/orgs': [] })
    signedIn()
    renderApp('/')
    expect(await screen.findByText('Polling')).toBeInTheDocument()
  })

  it('goes from connecting to live to reconnecting', async () => {
    const events = mockEvents()
    mockApi({ '/api/system/info': info(), '/api/orgs': [] }, events.handler)
    signedIn()
    renderApp('/')
    expect(await screen.findByText('Connecting…')).toBeInTheDocument()
    await vi.waitFor(() => expect(events.streams.length).toBe(1))
    act(() => events.latest()?.open())
    expect(await screen.findByText('Live')).toBeInTheDocument()
    act(() => events.latest()?.end())
    expect(await screen.findByText('Reconnecting…')).toBeInTheDocument()
  })

  it('is not shown on the sign-in page, where nothing is streamed', async () => {
    mockApi({})
    renderApp('/signin')
    await screen.findByLabelText('API key')
    expect(screen.queryByText('Polling')).not.toBeInTheDocument()
  })
})

describe('the chargers list on a live broker', () => {
  it('shows a charger the moment the broker says it connected, without waiting for the next refresh', async () => {
    let chargers = [charger()]
    const { events, fetch } = await live('/chargers', { '/api/orgs': [org()], '/api/chargers': () => list(...chargers) })
    await screen.findByRole('link', { name: 'CP-001' })
    const before = listCalls(fetch)
    chargers = [charger(), charger({ charger_id: 'CP-002' })]
    act(() => events.latest()?.emit(brokerEvent('charger.connected', {}, { charger_id: 'CP-002' })))
    expect(await screen.findByRole('link', { name: 'CP-002' })).toBeInTheDocument()
    expect(listCalls(fetch)).toBe(before + 1)
  })

  it('reads the list once for a burst of events', async () => {
    const { events, fetch } = await live('/chargers', { '/api/orgs': [org()], '/api/chargers': () => list(charger()) })
    await screen.findByRole('link', { name: 'CP-001' })
    const before = listCalls(fetch)
    act(() => {
      for (let n = 0; n < 20; n += 1) events.latest()?.emit(brokerEvent('charger.status', { connector_id: 1, status: 'Charging' }))
    })
    await vi.waitFor(() => expect(listCalls(fetch)).toBe(before + 1))
    await wait(500)
    expect(listCalls(fetch)).toBe(before + 1)
  })

  it('ignores another organization when it is filtered, and a command result, which changes no row', async () => {
    const { events, fetch } = await live('/chargers?org=Fleet', { '/api/orgs': [org()], '/api/chargers': () => list(charger()) })
    await screen.findByRole('link', { name: 'CP-001' })
    const before = listCalls(fetch)
    act(() => events.latest()?.emit(brokerEvent('charger.status', {}, { org: 'Home' })))
    act(() => events.latest()?.emit(brokerEvent('command.result', { action: 'Reset', status: 'success' })))
    await wait(500)
    expect(listCalls(fetch)).toBe(before)
    act(() => events.latest()?.emit(brokerEvent('charger.status', {}, { org: 'Fleet' })))
    await vi.waitFor(() => expect(listCalls(fetch)).toBe(before + 1))
  })

  it('reads the list of absent chargers again when a charger arrives or leaves', async () => {
    let gone = 0
    const absent = () => ({ available: true, reason: null, total: gone, chargers: [] })
    const { events, fetch } = await live('/chargers', { '/api/orgs': [org()], '/api/chargers': () => list(charger()), '/api/chargers/offline': absent })
    await screen.findByRole('link', { name: 'CP-001' })
    const absentCalls = () => fetch.mock.calls.filter(([url]) => String(url).startsWith('/api/chargers/offline')).length
    const before = absentCalls()
    act(() => events.latest()?.emit(brokerEvent('charger.status', { connector_id: 1, status: 'Charging', previous: null })))
    await wait(500)
    expect(absentCalls(), 'a status change moves nobody between the lists').toBe(before)
    gone = 1
    act(() => events.latest()?.emit(brokerEvent('charger.disconnected', { connected_for_seconds: 5 }, { charger_id: 'CP-009' })))
    await vi.waitFor(() => expect(absentCalls()).toBe(before + 1))
  })

  it('reloads after the broker was restarted', async () => {
    const { events, fetch } = await live('/chargers', { '/api/orgs': [org()], '/api/chargers': () => list(charger()) })
    await screen.findByRole('link', { name: 'CP-001' })
    const before = listCalls(fetch)
    act(() => events.latest()?.end())
    await vi.waitFor(() => expect(events.streams.length).toBe(2), { timeout: 3000 })
    act(() => events.latest()?.open('a-different-run'))
    await vi.waitFor(() => expect(listCalls(fetch)).toBe(before + 1))
  })
})

describe('a charger page on a live broker', () => {
  it('shows a new connector status as soon as the broker reports it', async () => {
    let connectors = [{ connector_id: 1, status: 'Available', error_code: 'NoError', info: null, updated_at: new Date().toISOString() }]
    const { events } = await live('/chargers/Fleet/CP-001', { [PATH]: () => detail({ connectors }) })
    const section = within(await screen.findByRole('region', { name: 'Connectors' }))
    expect(section.getByText('Available')).toBeInTheDocument()
    connectors = [{ ...connectors[0]!, status: 'Charging' }]
    act(() => events.latest()?.emit(brokerEvent('charger.status', { connector_id: 1, status: 'Charging', previous: 'Available', error_code: 'NoError' })))
    expect(await within(screen.getByRole('region', { name: 'Connectors' })).findByText('Charging')).toBeInTheDocument()
  })

  it('does not reload for another charger, and lists only its own events', async () => {
    const { events, fetch } = await live('/chargers/Fleet/CP-001', { [PATH]: detail() })
    await screen.findByRole('region', { name: 'Connectors' })
    const before = count(fetch, PATH)
    act(() => events.latest()?.emit(brokerEvent('charger.status', { connector_id: 1, status: 'Faulted', previous: 'Charging' }, { charger_id: 'CP-002' })))
    await wait(500)
    expect(count(fetch, PATH), 'another charger changed nothing here').toBe(before)
    act(() => events.latest()?.emit(brokerEvent('charger.status', { connector_id: 1, status: 'Unavailable', previous: 'Charging' })))
    const feed = within(await screen.findByRole('region', { name: 'Recent events for this charger' }))
    expect(await feed.findByText('connector 1: Charging → Unavailable')).toBeInTheDocument()
    expect(feed.queryByText(/Faulted/)).not.toBeInTheDocument()
    await vi.waitFor(() => expect(count(fetch, PATH)).toBe(before + 1))
    expect(feed.queryByRole('link', { name: 'CP-001' }), 'on its own page the charger is not named in each line').not.toBeInTheDocument()
  })

  it('explains an empty event list', async () => {
    await live('/chargers/Fleet/CP-001', { [PATH]: detail() })
    const feed = within(await screen.findByRole('region', { name: 'Recent events for this charger' }))
    expect(feed.getByText(/Nothing has happened since this page was opened/)).toBeInTheDocument()
  })

  it('starts with the recent history the broker replays on connecting', async () => {
    const events = mockEvents()
    mockApi({ [PATH]: detail() }, events.handler)
    signedIn()
    renderApp('/chargers/Fleet/CP-001')
    await vi.waitFor(() => expect(events.streams.length).toBe(1))
    act(() => {
      events.latest()?.open()
      events.latest()?.emit(brokerEvent('charger.connected', { mode: 'relay', ocpp_version: '1.6' }))
    })
    const feed = within(await screen.findByRole('region', { name: 'Recent events for this charger' }))
    expect(await feed.findByText('connected (relay, OCPP 1.6)')).toBeInTheDocument()
  })
})

describe('the overview on a live broker', () => {
  it('shows what is happening, naming the charger and linking to it', async () => {
    const { events } = await live('/', { '/api/system/info': info(), '/api/orgs': [org()] })
    act(() => {
      events.latest()?.emit(brokerEvent('backend.failover', { old_leader: 'primary', new_leader: 'standby' }, { charger_id: 'CP-007' }))
      events.latest()?.emit(brokerEvent('charger.disconnected', { connected_for_seconds: 90 }, { charger_id: 'CP-008' }))
    })
    const feed = within(await screen.findByRole('region', { name: 'Recent events' }))
    const items = await feed.findAllByRole('listitem')
    expect(items[0]).toHaveTextContent('CP-008 disconnected after 1m 30s')
    expect(items[1]).toHaveTextContent('CP-007 failover: standby took over from primary')
    expect(within(items[1]!).getByRole('link', { name: 'CP-007' })).toHaveAttribute('href', '/chargers/Fleet/CP-007')
  })

  it('refreshes its figures when a charger connects or leaves, and for nothing less', async () => {
    const { events, fetch } = await live('/', { '/api/system/info': info(), '/api/orgs': [org()] })
    await screen.findByText('Organizations', { selector: 'h2' })
    const before = count(fetch, '/api/system/info')
    act(() => events.latest()?.emit(brokerEvent('charger.status', {})))
    await wait(500)
    expect(count(fetch, '/api/system/info')).toBe(before)
    act(() => events.latest()?.emit(brokerEvent('charger.connected', {})))
    await vi.waitFor(() => expect(count(fetch, '/api/system/info')).toBe(before + 1))
    expect(count(fetch, '/api/orgs')).toBeGreaterThan(1)
  })

  it('says live events are not available when the broker has none', async () => {
    mockApi({ '/api/system/info': info(), '/api/orgs': [org()] })
    signedIn()
    renderApp('/')
    expect(await screen.findByText(/This broker does not offer live events/)).toBeInTheDocument()
  })

  it('shows only the latest 15', async () => {
    const { events } = await live('/', { '/api/system/info': info(), '/api/orgs': [org()] })
    act(() => {
      for (let n = 1; n <= 25; n += 1) events.latest()?.emit(brokerEvent('charger.status', { connector_id: n, status: 'Charging', previous: null }))
    })
    const feed = within(await screen.findByRole('region', { name: 'Recent events' }))
    await vi.waitFor(() => expect(feed.getAllByRole('listitem')).toHaveLength(15))
    expect(feed.getAllByRole('listitem')[0]).toHaveTextContent('connector 25')
  })
})
