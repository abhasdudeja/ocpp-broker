import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AuthProvider } from '../auth'
import { API_KEY, backendLink, mockFetch, respond, signedIn } from '../test-utils'
import { LeaderControl } from './LeaderControl'

const leader = backendLink({ key: 'primary' })
const standby = backendLink({ key: 'standby', role: 'follower' })

function show(backends = [leader, standby], onChanged = vi.fn()) {
  signedIn()
  render(
    <AuthProvider>
      <LeaderControl org="My Org" chargerId="CP/1" backends={backends} onChanged={onChanged} />
    </AuthProvider>,
  )
  return onChanged
}

async function confirm() {
  await userEvent.click(screen.getByRole('button', { name: 'Make standby the leader' }))
  await userEvent.click(within(screen.getByRole('group')).getByRole('button', { name: 'Make standby the leader' }))
}

describe('changing the leader by hand', () => {
  it('offers nothing when no follower is connected', () => {
    const { container } = render(
      <AuthProvider>
        <LeaderControl org="O" chargerId="C" backends={[leader, backendLink({ key: 'down', role: 'follower', connected: false })]} onChanged={() => undefined} />
      </AuthProvider>,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('offers each connected follower', () => {
    show([leader, standby, backendLink({ key: 'third', role: 'follower' })])
    expect(screen.getByRole('button', { name: 'Make standby the leader' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Make third the leader' })).toBeInTheDocument()
  })

  it('asks first, says what it means, and does nothing until it is confirmed', async () => {
    const fetch = mockFetch(() => respond({}))
    show()
    await userEvent.click(screen.getByRole('button', { name: 'Make standby the leader' }))
    const group = screen.getByRole('group', { name: 'Confirm standby' })
    expect(group).toHaveTextContent('may time out at the charger')
    expect(group).toHaveTextContent('not saved in the configuration')
    expect(fetch).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Keep the current leader' }))
    expect(screen.queryByRole('group')).not.toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('makes the change, with the key, to the charger’s own address, and tells the page to read again', async () => {
    const fetch = mockFetch(() => respond({ old_leader: 'primary', new_leader: 'standby' }))
    const onChanged = show()
    await confirm()
    expect(await screen.findByRole('status')).toHaveTextContent('standby leads now.')
    const [url, init] = fetch.mock.calls[0]!
    expect(String(url)).toBe('/api/chargers/My%20Org/CP%2F1/leader')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ backend: 'standby' })
    expect((init?.headers as Record<string, string>)['X-API-Key']).toBe(API_KEY)
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('shows why the broker refused', async () => {
    mockFetch(() => respond({ detail: 'standby is not connected, so it cannot take over' }, 409))
    const onChanged = show()
    await confirm()
    expect(await screen.findByRole('alert')).toHaveTextContent('standby is not connected')
    expect(onChanged).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Keep the current leader' })).toBeEnabled()
  })

  it('signs out when the broker stops accepting the key', async () => {
    mockFetch(() => respond({ detail: 'no' }, 401))
    show()
    await confirm()
    await waitFor(() => expect(window.sessionStorage.getItem('ocpp-broker-api-key')).toBeNull())
  })

  it('does not let a second change start while one is being made', async () => {
    let release: (response: Response) => void = () => undefined
    mockFetch(() => new Promise<Response>((resolve) => { release = resolve }) as unknown as Response)
    show([leader, standby, backendLink({ key: 'third', role: 'follower' })])
    await userEvent.click(screen.getByRole('button', { name: 'Make standby the leader' }))
    expect(screen.getByRole('button', { name: 'Make third the leader' })).toBeDisabled()
    await userEvent.click(within(screen.getByRole('group')).getByRole('button', { name: 'Make standby the leader' }))
    expect(screen.getByRole('button', { name: 'Changing…' })).toBeDisabled()
    release(respond({ old_leader: 'primary', new_leader: 'standby' }))
    await screen.findByRole('status')
  })
})
