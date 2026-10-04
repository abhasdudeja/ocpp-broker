import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { AppRoutes } from './App'
import type { SystemInfo } from './api/client'
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

export function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Replace fetch for one test; the handler gets the URL and the request init. */
export function mockFetch(handler: (url: string, init: RequestInit | undefined) => Response | Promise<Response>) {
  const fake = vi.fn((url: string | URL | Request, init?: RequestInit) => Promise.resolve(handler(String(url), init)))
  vi.stubGlobal('fetch', fake)
  return fake
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
