import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { API_KEY, info, mockApi, mockFetch, org, renderApp, respond, signedIn } from '../test-utils'

async function overview(body = info()) {
  signedIn()
  const fetch = mockApi({ '/api/system/info': body, '/api/orgs': [] })
  renderApp('/')
  await screen.findByRole('heading', { name: 'Overview' })
  await screen.findByText('Broker version')
  return fetch
}

describe('overview', () => {
  it('shows what this broker process is', async () => {
    const fetch = await overview()
    expect(screen.getByText('0.5')).toBeInTheDocument()
    expect(screen.getByText('abc12345')).toBeInTheDocument()
    expect(screen.getByText('1h 27m')).toBeInTheDocument()
    expect(screen.getByText('14')).toBeInTheDocument()
    expect(screen.getByText('on this instance')).toBeInTheDocument()
    expect(screen.getByText('API key required')).toBeInTheDocument()
    expect(screen.getByText('Connected (ocpp_broker)')).toBeInTheDocument()
    expect(fetch.mock.calls[0]![1]?.headers).toMatchObject({ 'X-API-Key': API_KEY })
    expect(screen.queryByText(/accepts requests without a key/)).not.toBeInTheDocument()
  })

  it.each([
    [{ configured: false, connected: false, reachable: false, database: null }, 'Not configured'],
    [{ configured: true, connected: true, reachable: false, database: 'x' }, 'Not answering'],
    [{ configured: true, connected: false, reachable: true, database: 'x' }, 'did not connect at startup'],
  ])('describes MongoDB as %j', async (mongodb, text) => {
    await overview(info({ mongodb }))
    expect(screen.getByText(new RegExp(text))).toBeInTheDocument()
  })

  it('warns when MongoDB is configured but not answering, and when the API is open', async () => {
    await overview(
      info({ api_auth: 'none', mongodb: { configured: true, connected: true, reachable: false, database: 'x' } }),
    )
    expect(screen.getByText(/MongoDB is configured but not answering/)).toBeInTheDocument()
    expect(screen.getByText(/accepts requests without a key/)).toBeInTheDocument()
    expect(screen.getByText('Open (no key)')).toBeInTheDocument()
  })

  it('does not warn about MongoDB when it is simply not configured', async () => {
    await overview(info({ mongodb: { configured: false, connected: false, reachable: false, database: null } }))
    expect(screen.queryByText(/MongoDB is configured/)).not.toBeInTheDocument()
  })

  it('refreshes on its own every ten seconds and stops when it goes away', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    signedIn()
    const fetch = mockApi({ '/api/system/info': info(), '/api/orgs': [] })
    const calls = (path: string) => fetch.mock.calls.filter(([url]) => url === path).length
    const { unmount } = renderApp('/')
    await screen.findByText('Broker version')
    expect([calls('/api/system/info'), calls('/api/orgs')]).toEqual([1, 1])

    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect([calls('/api/system/info'), calls('/api/orgs')]).toEqual([2, 2])
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect([calls('/api/system/info'), calls('/api/orgs')]).toEqual([3, 3])

    unmount()
    await vi.advanceTimersByTimeAsync(60_000)
    expect([calls('/api/system/info'), calls('/api/orgs')]).toEqual([3, 3])
  })

  it('keeps the last figures on screen and says so when a refresh fails', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    signedIn()
    let healthy = true
    const unlessDown = (body: unknown) => () => {
      if (!healthy) throw new TypeError('Failed to fetch')
      return body
    }
    mockApi({ '/api/system/info': unlessDown(info()), '/api/orgs': unlessDown([]) })
    renderApp('/')
    await screen.findByText('Broker version')

    healthy = false
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not refresh: Could not reach the broker')
    expect(screen.getByText('abc12345')).toBeInTheDocument()

    healthy = true
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says it could not load, rather than showing an empty page, if the first call fails', async () => {
    signedIn()
    mockFetch(() => respond({ detail: 'boom' }, 500))
    renderApp('/')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load: boom')
  })

  it('goes back to sign-in, with a reason, when the broker stops accepting the key', async () => {
    signedIn()
    mockFetch(() => respond({ detail: 'Missing or invalid API key' }, 401))
    renderApp('/')
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
    expect(screen.getByText('The broker no longer accepts this key. Sign in again.')).toBeInTheDocument()
    expect(window.sessionStorage.getItem('ocpp-broker-api-key')).toBeNull()
  })

  it('lists the organizations with their mode, backends and connected chargers', async () => {
    signedIn()
    mockApi({
      '/api/system/info': info(),
      '/api/orgs': [
        org(),
        org({ name: 'Home', mode: 'broker', connected_chargers: 0, backends: [], transaction_id_mapping: false }),
        org({ name: 'Depot', charger_auth_required: true, transaction_id_mapping: false, backends: [org().backends[0]!], connected_chargers: 1 }),
      ],
    })
    renderApp('/')
    const table = within(await screen.findByRole('table'))
    expect(table.getAllByRole('rowheader').map((h) => h.textContent)).toEqual(['Fleet', 'Home', 'Depot'])

    const fleet = within(table.getByRole('row', { name: /Fleet/ }))
    expect(fleet.getByRole('link', { name: '2' })).toHaveAttribute('href', '/chargers?org=Fleet')
    expect(fleet.getByText('leader')).toBeInTheDocument()
    expect(fleet.getByText('ids translated')).toBeInTheDocument()
    expect(fleet.getByText(/standby/)).toBeInTheDocument()

    const home = within(table.getByRole('row', { name: /Home/ }))
    expect(home.getByText('this broker')).toBeInTheDocument()
    expect(home.queryByRole('link')).not.toBeInTheDocument()

    const depot = within(table.getByRole('row', { name: /Depot/ }))
    expect(depot.getByText('required')).toBeInTheDocument()
    expect(depot.queryByText('ids translated')).not.toBeInTheDocument()
  })

  it('still shows the figures if the organizations answer is not a list', async () => {
    signedIn()
    mockApi({ '/api/system/info': info(), '/api/orgs': { unexpected: true } })
    renderApp('/')
    expect(await screen.findByText('Broker version')).toBeInTheDocument()
    expect(screen.queryByText('This page could not be shown.')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('says when no organization is configured', async () => {
    signedIn()
    mockApi({ '/api/system/info': info({ organizations: 0 }), '/api/orgs': [] })
    renderApp('/')
    expect(await screen.findByText(/No organizations are configured/)).toBeInTheDocument()
  })

  it('signs out', async () => {
    await overview()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
    expect(window.sessionStorage.getItem('ocpp-broker-api-key')).toBeNull()
  })
})
