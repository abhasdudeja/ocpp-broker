import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { API_KEY, info, mockBroker, mockFetch, renderApp, respond, signedIn } from '../test-utils'

async function submit(key: string) {
  const user = userEvent.setup()
  if (key) await user.type(screen.getByLabelText('API key'), key)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('sign-in', () => {
  it('is where an unauthenticated visitor lands, whatever page they asked for', () => {
    mockBroker()
    renderApp('/')
    expect(screen.getByRole('heading', { name: 'OCPP Broker' })).toBeInTheDocument()
    expect(screen.getByLabelText('API key')).toHaveAttribute('type', 'password')
  })

  it('asks for a key before it asks the broker anything', async () => {
    const fetch = mockBroker()
    renderApp('/signin')
    await submit('')
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter the API key.')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('says so when the broker does not accept the key, and keeps nothing', async () => {
    mockFetch(() => respond({ detail: 'Missing or invalid API key' }, 401))
    renderApp('/signin')
    await submit('wrong')
    expect(await screen.findByRole('alert')).toHaveTextContent('The broker did not accept that key.')
    expect(window.sessionStorage.getItem('ocpp-broker-api-key')).toBeNull()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('tells the reader to wait when the broker has blocked this address for wrong keys', async () => {
    mockFetch(() => respond({ detail: 'Too many wrong API keys from this address; try again in 42 s' }, 429))
    renderApp('/signin')
    await submit('guess')
    expect(await screen.findByRole('alert')).toHaveTextContent('try again in 42 s')
    expect(window.sessionStorage.getItem('ocpp-broker-api-key')).toBeNull()
  })

  it('explains a broker that has no API key configured', async () => {
    mockFetch(() => respond({ detail: 'REST API disabled: set security.api_key or OCPP_BROKER_API_KEY' }, 503))
    renderApp('/signin')
    await submit('anything')
    expect(await screen.findByRole('alert')).toHaveTextContent('no API key configured')
  })

  it('explains a broker that cannot be reached', async () => {
    mockFetch(() => {
      throw new TypeError('Failed to fetch')
    })
    renderApp('/signin')
    await submit('anything')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the broker')
  })

  it('checks the key with the system info call, trimmed, in the header', async () => {
    const fetch = mockBroker()
    renderApp('/signin')
    await submit(`  ${API_KEY}  `)
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/system/info')
    expect(init?.headers).toMatchObject({ 'X-API-Key': API_KEY })
    expect(window.sessionStorage.getItem('ocpp-broker-api-key')).toBe(API_KEY)
  })

  it('does not leave the key anywhere in the page once signed in', async () => {
    mockBroker()
    const { container } = renderApp('/signin')
    await submit(API_KEY)
    await screen.findByRole('heading', { name: 'Overview' })
    expect(container.innerHTML).not.toContain(API_KEY)
  })

  it('cannot be submitted twice while the key is being checked', async () => {
    let release: (value: Response) => void = () => {}
    const fetch = mockFetch(() => new Promise<Response>((resolve) => (release = resolve)) as unknown as Response)
    renderApp('/signin')
    await submit(API_KEY)
    expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled()
    release(respond(info()))
    await screen.findByRole('heading', { name: 'Overview' })
    // sign-in check, then the page's own load (which starts a moment after the page is shown)
    await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url === '/api/system/info').length).toBe(2))
  })

  it('sends someone who is already signed in straight to the overview', async () => {
    signedIn()
    mockBroker()
    renderApp('/signin')
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
  })

  it('treats an unknown address as the overview: sign in first, then land there', async () => {
    mockBroker()
    renderApp('/unknown/page')
    await submit(API_KEY)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Overview' })).toBeInTheDocument())
  })
})
