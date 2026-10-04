import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { AppRoutes } from './App'
import type { BackendLink, BrokerEvent, ChargerDetail, ChargerSummary, CommandCatalog, OrgSummary, SystemInfo } from './api/client'
import { AuthProvider } from './auth'
import commandCatalog from './test-fixtures/command-catalog.json'

export const API_KEY = 'test-key'

export function info(overrides: Partial<SystemInfo> = {}): SystemInfo {
  return {
    version: '0.5',
    instance_id: 'abc12345',
    started_at: '2026-10-04T08:00:00Z',
    uptime_seconds: 5231.4,
    api_auth: 'api_key',
    ui_enabled: true,
    ui_built: true,
    organizations: 2,
    connected_chargers: 14,
    mongodb: { configured: true, connected: true, reachable: true, database: 'ocpp_broker' },
    ...overrides,
  }
}

export function backendLink(overrides: Partial<BackendLink> = {}): BackendLink {
  return {
    key: 'primary',
    url: 'ws://primary.example.com/ocpp',
    role: 'leader',
    local: false,
    connected: true,
    buffered_frames: 0,
    down_for_seconds: null,
    ...overrides,
  }
}

export function ago(seconds: number): string {
  return new Date(Date.now() - seconds * 1000).toISOString()
}

export function charger(overrides: Partial<ChargerSummary> = {}): ChargerSummary {
  return {
    org: 'Fleet',
    charger_id: 'CP-001',
    online: true,
    mode: 'relay',
    ocpp_version: '1.6',
    connected_at: ago(600),
    last_seen: ago(30),
    remote_address: '10.0.0.5:51234',
    vendor: 'Acme',
    model: 'Wallbox 7',
    firmware_version: '2.1.0',
    connector_statuses: { '1': 'Charging' },
    leader: backendLink(),
    followers_total: 1,
    followers_connected: 1,
    buffered_frames: 0,
    open_transactions: 0,
    degraded_transactions: 0,
    ...overrides,
  }
}

export function detail(overrides: Partial<ChargerDetail> = {}): ChargerDetail {
  return {
    ...charger(),
    boot: {
      vendor: 'Acme',
      model: 'Wallbox 7',
      serial_number: 'SN-7',
      firmware_version: '2.1.0',
      iccid: '8931088',
      imsi: null,
      meter_type: 'EM-3',
      meter_serial_number: null,
      received_at: ago(600),
    },
    last_heartbeat_at: ago(20),
    connectors: [{ connector_id: 1, status: 'Charging', error_code: 'NoError', info: null, updated_at: ago(90) }],
    backends: [backendLink(), backendLink({ key: 'standby', url: 'ws://standby.example.com/ocpp', role: 'follower' })],
    transaction_id_mapping: true,
    transactions: [],
    reservations: [],
    charging_profiles: [],
    frames_in: 12,
    frames_out: 11,
    id_table_stats: {},
    ...overrides,
  }
}

export function org(overrides: Partial<OrgSummary> = {}): OrgSummary {
  return {
    name: 'Fleet',
    mode: 'relay',
    ocpp_version: '1.6',
    charger_auth_required: false,
    connected_chargers: 2,
    backends: [
      { key: 'primary', url: 'ws://primary.example.com/ocpp', local: false, leader: true, ocpp_subprotocol: 'ocpp1.6' },
      { key: 'standby', url: 'ws://standby.example.com/ocpp', local: false, leader: false, ocpp_subprotocol: 'ocpp1.6' },
    ],
    transaction_id_mapping: true,
    ...overrides,
  }
}

/**
 * Answer fetches by URL prefix (the longest matching prefix wins). A value that is not a Response is sent as
 * JSON; a function is called each time, so a test can change what the broker says.
 */
export function mockApi(routes: Record<string, unknown | (() => unknown)>, events?: (init: RequestInit | undefined) => Response) {
  const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length)
  // Answers for calls every charger page makes, unless a test mocks that exact address itself
  const defaults: Array<[RegExp, unknown]> = [
    [/^\/api\/chargers\/[^/]+\/[^/]+\/commands$/, { commands: [] }],
    [/^\/api\/ocpp\/commands\/catalog$/, catalog()],
    [/^\/api\/chargers\/offline/, { available: false, reason: 'MongoDB is not configured', chargers: [], total: 0 }],
  ]
  return mockFetch((url, init) => {
    if (url.startsWith('/api/events')) return events ? events(init) : respond({ detail: 'no event stream' }, 404)
    const prefix = prefixes.find((p) => url.startsWith(p))
    const standard = defaults.find(([pattern]) => pattern.test(url) && (prefix === undefined || prefix.length < url.length))
    if (standard) return respond(standard[1])
    if (prefix === undefined) return respond({ detail: `no mock for ${url}` }, 404)
    const route = routes[prefix]
    const value = typeof route === 'function' ? (route as () => unknown)() : route
    return value instanceof Response ? value : respond(value)
  })
}

export function catalog(): CommandCatalog {
  return commandCatalog as unknown as CommandCatalog
}

let eventCounter = 0

/** An event as the broker sends it. */
export function brokerEvent(type: BrokerEvent['type'], data: Record<string, unknown> = {}, who: { org?: string | null; charger_id?: string | null } = {}): BrokerEvent {
  eventCounter += 1
  return {
    id: eventCounter,
    type,
    time: new Date().toISOString(),
    org: who.org === undefined ? 'Fleet' : who.org,
    charger_id: who.charger_id === undefined ? 'CP-001' : who.charger_id,
    data,
  }
}

/** The text of one SSE message. */
export function sse(event: string, data: unknown, id?: number): string {
  return `${id === undefined ? '' : `id: ${id}\n`}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

/**
 * A fake /api/events response the test writes to. Aborting the request (as the console does when it
 * goes away) errors the stream, as a real fetch would.
 */
export class FakeStream {
  readonly response: Response
  private controller!: ReadableStreamDefaultController<Uint8Array>
  private readonly encoder = new TextEncoder()
  closed = false

  constructor(signal?: AbortSignal | null) {
    const body = new ReadableStream<Uint8Array>({
      start: (controller) => {
        this.controller = controller
      },
    })
    this.response = new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    signal?.addEventListener('abort', () => {
      this.closed = true
      try {
        this.controller.error(new DOMException('Aborted', 'AbortError'))
      } catch {
        // already finished
      }
    })
  }

  send(text: string) {
    this.controller.enqueue(this.encoder.encode(text))
  }

  open(instance = 'run-1', missed = false) {
    this.send(sse('stream.open', { instance_id: instance, last_id: 0, missed }))
  }

  emit(event: BrokerEvent) {
    this.send(sse(event.type, event, event.id))
  }

  end() {
    this.closed = true
    this.controller.close()
  }
}

/** Streams handed out by ``mockEvents``, one per connection the console makes, with the request each came from. */
export function mockEvents() {
  const streams: FakeStream[] = []
  const requests: RequestInit[] = []
  const handler = (init: RequestInit | undefined) => {
    const stream = new FakeStream(init?.signal)
    streams.push(stream)
    requests.push(init ?? {})
    return stream.response
  }
  return { handler, streams, requests, latest: () => streams[streams.length - 1] }
}

export function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Replace fetch for one test; the handler gets the URL and the request init. */
export function mockFetch(handler: (url: string, init: RequestInit | undefined) => Response | Promise<Response>) {
  const fake = vi.fn((url: string | URL | Request, init?: RequestInit) => Promise.resolve(handler(String(url), init)))
  vi.stubGlobal('fetch', fake)
  return fake
}

/** A broker that answers the calls the overview makes (and nothing else). */
export function mockBroker(systemInfo: SystemInfo = info(), orgs: OrgSummary[] = []) {
  return mockApi({ '/api/system/info': systemInfo, '/api/orgs': orgs })
}

export function renderApp(path = '/') {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthProvider>,
  )
}

export function signedIn() {
  window.sessionStorage.setItem('ocpp-broker-api-key', API_KEY)
}
