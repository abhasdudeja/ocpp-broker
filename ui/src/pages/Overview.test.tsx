import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { API_KEY, info, mockFetch, renderApp, respond, signedIn } from '../test-utils'

async function overview(body = info()) {
  signedIn()
  const fetch = mockFetch(() => respond(body))
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
    const fetch = mockFetch(() => respond(info()))
    const { unmount } = renderApp('/')
    await screen.findByText('Broker version')
    expect(fetch).toHaveBeenCalledTimes(1)

    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(fetch).toHaveBeenCalledTimes(2)
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(fetch).toHaveBeenCalledTimes(3)

    unmount()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('keeps the last figures on screen and says so when a refresh fails', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    signedIn()
    let healthy = true
    mockFetch(() => {
      if (!healthy) throw new TypeError('Failed to fetch')
      return respond(info())
    })
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

  it('signs out', async () => {
    await overview()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
    expect(window.sessionStorage.getItem('ocpp-broker-api-key')).toBeNull()
  })
})
