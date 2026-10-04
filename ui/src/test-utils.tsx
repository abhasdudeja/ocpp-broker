import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { AppRoutes } from './App'
import type { BackendLink, ChargerDetail, ChargerSummary, OrgSummary, SystemInfo } from './api/client'
import { AuthProvider } from './auth'

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
      { key: 'primary', url: 'ws://primary.example.com/ocpp', leader: true, ocpp_subprotocol: 'ocpp1.6' },
      { key: 'standby', url: 'ws://standby.example.com/ocpp', leader: false, ocpp_subprotocol: 'ocpp1.6' },
    ],
    transaction_id_mapping: true,
    ...overrides,
  }
}

/**
 * Answer fetches by URL prefix (the longest matching prefix wins). A value that is not a Response is sent as
 * JSON; a function is called each time, so a test can change what the broker says.
 */
export function mockApi(routes: Record<string, unknown | (() => unknown)>) {
  const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length)
  return mockFetch((url) => {
    const prefix = prefixes.find((p) => url.startsWith(p))
    if (prefix === undefined) return respond({ detail: `no mock for ${url}` }, 404)
    const route = routes[prefix]
    const value = typeof route === 'function' ? (route as () => unknown)() : route
    return value instanceof Response ? value : respond(value)
  })
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
