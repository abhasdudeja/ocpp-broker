import { act, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { AuthProvider } from './auth'
import { EventsProvider, RECENT_LIMIT, useEvents, useRefreshOnEvents, type StreamEvent } from './events'
import { API_KEY, brokerEvent, mockApi, mockEvents, respond, signedIn, sse, type FakeStream } from './test-utils'

function Probe({ seen }: { seen?: StreamEvent[] }) {
  const { status, recent, subscribe } = useEvents()
  useEffect(() => (seen ? subscribe((event) => seen.push(event)) : undefined), [seen, subscribe])
  return (
    <div>
      <span data-testid="status">{status}</span>
      <ol aria-label="recent">
        {recent.map((e) => (
          <li key={e.id}>{e.type}#{e.id}</li>
        ))}
      </ol>
    </div>
  )
}

function mount(seen?: StreamEvent[]) {
  signedIn()
  return render(
    <AuthProvider>
      <MemoryRouter>
        <EventsProvider>
          <Probe seen={seen} />
        </EventsProvider>
      </MemoryRouter>
    </AuthProvider>,
  )
}

const status = () => screen.getByTestId('status').textContent
const recent = () => screen.getAllByRole('listitem').map((li) => li.textContent)

async function opened(events: ReturnType<typeof mockEvents>, instance = 'run-1', missed = false) {
  await vi.waitFor(() => expect(events.streams.length).toBeGreaterThan(0))
  act(() => events.latest()?.open(instance, missed))
  await vi.waitFor(() => expect(status()).toBe('live'))
}

const send = (stream: FakeStream | undefined, ...items: ReturnType<typeof brokerEvent>[]) => act(() => items.forEach((e) => stream?.emit(e)))

describe('the event stream', () => {
  it('asks for the stream with the key in a header, never in the address', async () => {
    const events = mockEvents()
    const fetch = mockApi({}, events.handler)
    mount()
    await vi.waitFor(() => expect(events.requests).toHaveLength(1))
    const [url, init] = fetch.mock.calls[0] ?? []
    expect(String(url)).toBe('/api/events?replay=100')
    expect(String(url)).not.toContain(API_KEY)
    expect(init?.headers).toMatchObject({ 'X-API-Key': API_KEY, Accept: 'text/event-stream' })
    expect(init?.headers).not.toHaveProperty('Last-Event-ID')
  })

  it('is connecting until the broker says hello, then live', async () => {
    const events = mockEvents()
    mockApi({}, events.handler)
    mount()
    expect(status()).toBe('connecting')
    await opened(events)
    expect(status()).toBe('live')
  })

  it('collects events newest first and tells subscribers about each one as it arrives', async () => {
    const events = mockEvents()
    mockApi({}, events.handler)
    const seen: StreamEvent[] = []
    mount(seen)
    await opened(events)
    const [a, b, c] = [brokerEvent('charger.connected'), brokerEvent('charger.boot'), brokerEvent('charger.status')]
    send(events.latest(), a, b, c)
    await vi.waitFor(() => expect(recent()).toEqual([`charger.status#${c.id}`, `charger.boot#${b.id}`, `charger.connected#${a.id}`]))
    expect(seen.map((e) => e.type)).toEqual(['charger.connected', 'charger.boot', 'charger.status'])
  })

  it('keeps only the most recent events', async () => {
    const events = mockEvents()
    mockApi({}, events.handler)
    mount()
    await opened(events)
    const batch = Array.from({ length: RECENT_LIMIT + 20 }, () => brokerEvent('charger.status'))
    send(events.latest(), ...batch)
    await vi.waitFor(() => expect(recent()).toHaveLength(RECENT_LIMIT))
    expect(recent()[0]).toBe(`charger.status#${batch[batch.length - 1]?.id}`)
  })

  it('skips a message it cannot read and carries on', async () => {
    const events = mockEvents()
    mockApi({}, events.handler)
    mount()
    await opened(events)
    act(() => events.latest()?.send('event: charger.status\ndata: {broken\n\nevent: charger.status\ndata: {"id":"x"}\n\n'))
    const good = brokerEvent('charger.boot')
    send(events.latest(), good)
    await vi.waitFor(() => expect(recent()).toEqual([`charger.boot#${good.id}`]))
    expect(status()).toBe('live')
  })

  it('goes on delivering events to the other listeners when one of them throws', async () => {
    const events = mockEvents()
    mockApi({}, events.handler)
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const seen: StreamEvent[] = []
    function Bad() {
      const { subscribe } = useEvents()
      useEffect(
        () =>
          subscribe(() => {
            throw new Error('listener bug')
          }),
        [subscribe],
      )
      return null
    }
    signedIn()
    render(
      <AuthProvider>
        <MemoryRouter>
          <EventsProvider>
            <Bad />
            <Probe seen={seen} />
          </EventsProvider>
        </MemoryRouter>
      </AuthProvider>,
    )
    await opened(events)
    send(events.latest(), brokerEvent('charger.boot'))
    await vi.waitFor(() => expect(seen).toHaveLength(1))
  })

  it('reconnects when the stream ends, asking only for what it has not seen', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const events = mockEvents()
    mockApi({}, events.handler)
    mount()
    await opened(events)
    const last = brokerEvent('charger.boot')
    send(events.latest(), last)
    await vi.waitFor(() => expect(recent()).toHaveLength(1))
    act(() => events.latest()?.end())
    await vi.waitFor(() => expect(status()).toBe('reconnecting'))
    await act(() => vi.advanceTimersByTimeAsync(1_100))
    await vi.waitFor(() => expect(events.requests).toHaveLength(2))
    expect(events.requests[1]?.headers).toMatchObject({ 'Last-Event-ID': String(last.id) })
    await opened(events)
  })

  it('waits longer after each failure, up to a limit, and starts again after a success', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const events = mockEvents()
    let failing = true
    const fetch = mockApi({}, (init) => (failing ? respond({ detail: 'busy' }, 503) : events.handler(init)))
    mount()
    const attempts = () => fetch.mock.calls.filter(([url]) => String(url).startsWith('/api/events')).length
    await vi.waitFor(() => expect(attempts()).toBe(1))
    for (const [wait, expected] of [[900, 1], [200, 2], [1_700, 2], [400, 3], [3_600, 3], [400, 4]] as const) {
      await act(() => vi.advanceTimersByTimeAsync(wait))
      expect(attempts(), `after waiting ${wait}`).toBe(expected)
    }
    expect(status()).toBe('reconnecting')
    failing = false
    await act(() => vi.advanceTimersByTimeAsync(8_100))
    await opened(events)
    act(() => events.latest()?.end())
    await act(() => vi.advanceTimersByTimeAsync(1_100))
    await vi.waitFor(() => expect(events.requests).toHaveLength(2))
  })

  it('never waits more than 30 seconds between attempts', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetch = mockApi({}, () => respond({ detail: 'busy' }, 503))
    mount()
    const attempts = () => fetch.mock.calls.filter(([url]) => String(url).startsWith('/api/events')).length
    await vi.waitFor(() => expect(attempts()).toBe(1))
    // attempts happen at 0, 1, 3, 7, 15 and 31 seconds, then every 30 seconds
    for (const [seconds, expected] of [[31.5, 6], [29, 6], [1, 7], [29, 7], [1, 8]] as const) {
      await act(() => vi.advanceTimersByTimeAsync(seconds * 1000))
      expect(attempts(), `${seconds} s later`).toBe(expected)
    }
  })

  it('treats a stream that goes silent as dead and reconnects', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const events = mockEvents()
    mockApi({}, events.handler)
    mount()
    await opened(events)
    await act(() => vi.advanceTimersByTimeAsync(14_000))
    act(() => events.latest()?.send(': keepalive\n\n'))
    await act(() => vi.advanceTimersByTimeAsync(40_000))
    expect(events.requests, 'a keepalive resets the clock').toHaveLength(1)
    await act(() => vi.advanceTimersByTimeAsync(6_000))
    await vi.waitFor(() => expect(status()).toBe('reconnecting'))
    await act(() => vi.advanceTimersByTimeAsync(1_100))
    await vi.waitFor(() => expect(events.requests).toHaveLength(2))
  })

  it('tells listeners to reload when the broker was restarted', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const events = mockEvents()
    mockApi({}, events.handler)
    const seen: StreamEvent[] = []
    mount(seen)
    await opened(events, 'run-1')
    send(events.latest(), brokerEvent('charger.boot'))
    await vi.waitFor(() => expect(recent()).toHaveLength(1))
    act(() => events.latest()?.end())
    await act(() => vi.advanceTimersByTimeAsync(1_100))
    await vi.waitFor(() => expect(events.requests).toHaveLength(2))
    expect(events.requests[1]?.headers).toHaveProperty('Last-Event-ID')
    act(() => events.latest()?.open('run-2'))
    await vi.waitFor(() => expect(seen.map((e) => e.type)).toContain('stream.reset'))
    await vi.waitFor(() => expect(screen.queryAllByRole('listitem')).toHaveLength(0))
  })

  it('tells listeners to reload when events were missed, and not otherwise', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const events = mockEvents()
    mockApi({}, events.handler)
    const seen: StreamEvent[] = []
    mount(seen)
    await opened(events, 'run-1', false)
    expect(seen).toEqual([])
    act(() => events.latest()?.end())
    await act(() => vi.advanceTimersByTimeAsync(1_100))
    await vi.waitFor(() => expect(events.requests).toHaveLength(2))
    act(() => events.latest()?.open('run-1', true))
    await vi.waitFor(() => expect(seen).toEqual([{ type: 'stream.reset' }]))
  })

  it('stops trying when this broker has no event stream, and says so', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetch = mockApi({})
    mount()
    await vi.waitFor(() => expect(status()).toBe('unavailable'))
    await act(() => vi.advanceTimersByTimeAsync(120_000))
    expect(fetch.mock.calls.filter(([url]) => String(url).startsWith('/api/events'))).toHaveLength(1)
  })

  it('signs out when the broker no longer accepts the key', async () => {
    mockApi({}, () => respond({ detail: 'no' }, 401))
    mount()
    await vi.waitFor(() => expect(window.sessionStorage.getItem('ocpp-broker-api-key')).toBeNull())
  })

  it('closes the stream when the console goes away', async () => {
    const events = mockEvents()
    mockApi({}, events.handler)
    const { unmount } = mount()
    await opened(events)
    unmount()
    await vi.waitFor(() => expect(events.latest()?.closed).toBe(true))
  })

  it('does not open a stream when nobody is signed in', async () => {
    const events = mockEvents()
    const fetch = mockApi({}, events.handler)
    render(
      <AuthProvider>
        <MemoryRouter>
          <EventsProvider>
            <Probe />
          </EventsProvider>
        </MemoryRouter>
      </AuthProvider>,
    )
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)))
    expect(fetch).not.toHaveBeenCalled()
  })
})

describe('useRefreshOnEvents', () => {
  function Refresher({ reload, wanted }: { reload: () => void; wanted: string }) {
    useRefreshOnEvents(reload, (e) => e.charger_id === wanted, 50)
    return null
  }

  async function withRefresher(wanted = 'CP-001') {
    const events = mockEvents()
    mockApi({}, events.handler)
    const reload = vi.fn()
    signedIn()
    render(
      <AuthProvider>
        <MemoryRouter>
          <EventsProvider>
            <Probe />
            <Refresher reload={reload} wanted={wanted} />
          </EventsProvider>
        </MemoryRouter>
      </AuthProvider>,
    )
    await opened(events)
    return { events, reload }
  }

  it('reloads once for a burst of matching events', async () => {
    const { events, reload } = await withRefresher()
    send(events.latest(), brokerEvent('charger.status'), brokerEvent('charger.status'), brokerEvent('charger.status'))
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1))
    await act(() => new Promise((resolve) => setTimeout(resolve, 150)))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('ignores events that do not match', async () => {
    const { events, reload } = await withRefresher()
    send(events.latest(), brokerEvent('charger.status', {}, { charger_id: 'CP-002' }))
    await act(() => new Promise((resolve) => setTimeout(resolve, 150)))
    expect(reload).not.toHaveBeenCalled()
  })

  it('reloads after a reset whatever the filter says', async () => {
    const { events, reload } = await withRefresher()
    act(() => events.latest()?.open('another-run'))
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1))
  })

  it('reloads again for a later event', async () => {
    const { events, reload } = await withRefresher()
    send(events.latest(), brokerEvent('charger.status'))
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1))
    send(events.latest(), brokerEvent('charger.status'))
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(2))
  })

  it('uses the filter it was last given', async () => {
    const events = mockEvents()
    mockApi({}, events.handler)
    const reload = vi.fn()
    signedIn()
    const tree = (wanted: string) => (
      <AuthProvider>
        <MemoryRouter>
          <EventsProvider>
            <Probe />
            <Refresher reload={reload} wanted={wanted} />
          </EventsProvider>
        </MemoryRouter>
      </AuthProvider>
    )
    const { rerender } = render(tree('CP-001'))
    await opened(events)
    rerender(tree('CP-009'))
    send(events.latest(), brokerEvent('charger.status', {}, { charger_id: 'CP-009' }))
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1))
  })
})

describe('what an event stream message looks like', () => {
  it('is exactly what the broker sends', () => {
    // The text the broker's events_api produces: id, event name, one data line, a blank line
    expect(sse('charger.status', { id: 3 }, 3)).toBe('id: 3\nevent: charger.status\ndata: {"id":3}\n\n')
  })
})
