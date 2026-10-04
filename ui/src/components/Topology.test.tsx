import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { backendLink } from '../test-utils'
import { Topology } from './Topology'

const follower = backendLink({ key: 'standby', url: 'ws://standby/ocpp', role: 'follower' })

describe('Topology', () => {
  it('shows the charger, the broker and each backend with its role and link state', () => {
    render(<Topology chargerId="CP-001" mode="relay" backends={[backendLink(), follower]} />)
    const backends = within(screen.getByRole('list', { name: 'Backends' }))
    expect(backends.getAllByRole('listitem')).toHaveLength(2)
    expect(backends.getByText('leader')).toBeInTheDocument()
    expect(backends.getByText('follower')).toBeInTheDocument()
    expect(backends.getByText('ws://primary.example.com/ocpp')).toBeInTheDocument()
    expect(backends.getAllByText('Connected')).toHaveLength(2)
    expect(screen.getByText('relays and observes')).toBeInTheDocument()
  })

  it('says a backend is not connected, and for how long', () => {
    render(
      <Topology
        chargerId="CP-001"
        mode="relay"
        backends={[backendLink({ connected: false, down_for_seconds: 12.4 }), follower]}
      />,
    )
    expect(screen.getByText('Not connected')).toBeInTheDocument()
    expect(screen.getByText(/for 12 s/)).toBeInTheDocument()
  })

  it('marks a backend that is down so it stands out', () => {
    render(<Topology chargerId="C" mode="relay" backends={[backendLink({ connected: false }), follower]} />)
    const [leader, standby] = within(screen.getByRole('list', { name: 'Backends' })).getAllByRole('listitem')
    expect(leader).toHaveClass('is-down')
    expect(standby).toHaveClass('is-up')
  })

  it('shows how many charger messages are waiting for an unreachable leader', () => {
    const { rerender } = render(<Topology chargerId="C" mode="relay" backends={[backendLink({ connected: false, buffered_frames: 1 })]} />)
    expect(screen.getByText('1 message waiting')).toBeInTheDocument()
    rerender(<Topology chargerId="C" mode="relay" backends={[backendLink({ connected: false, buffered_frames: 7 })]} />)
    expect(screen.getByText('7 messages waiting')).toBeInTheDocument()
    rerender(<Topology chargerId="C" mode="relay" backends={[backendLink()]} />)
    expect(screen.queryByText(/waiting/)).not.toBeInTheDocument()
  })

  it('shows the broker itself as the backend in broker mode', () => {
    render(
      <Topology
        chargerId="WB-01"
        mode="broker"
        backends={[backendLink({ key: 'broker', url: null, local: true })]}
      />,
    )
    expect(screen.getByText('this broker')).toBeInTheDocument()
    expect(screen.getByText('answers the charger')).toBeInTheDocument()
  })

  it('shows the broker as the leader with followers that only receive copies', () => {
    const local = backendLink({ key: 'broker', url: null, local: true })
    const { container } = render(<Topology chargerId="CP-001" mode="broker" backends={[local, follower]} />)
    const backends = within(screen.getByRole('list', { name: 'Backends' }))
    expect(backends.getAllByRole('listitem').map((li) => li.querySelector('strong')?.textContent)).toEqual(['broker', 'standby'])
    expect(backends.getByText('this broker')).toBeInTheDocument()
    expect(screen.getByText('answers and copies to followers')).toBeInTheDocument()
    expect(screen.queryByText('answers the charger')).not.toBeInTheDocument()
    expect(container.querySelector('figcaption')).toHaveTextContent(
      'CP-001 is connected to the broker, which answers it itself and sends copies to follower standby (connected).',
    )
  })

  it('has a text description for people who cannot see the diagram', () => {
    const { container } = render(<Topology chargerId="CP-001" mode="relay" backends={[backendLink({ connected: false }), follower]} />)
    expect(container.querySelector('figcaption')).toHaveTextContent(
      'CP-001 is connected to the broker, which forwards to leader primary (not connected) and follower standby (connected).',
    )
    const { container: broker } = render(
      <Topology chargerId="WB-01" mode="broker" backends={[backendLink({ key: 'broker', url: null, local: true })]} />,
    )
    expect(broker.querySelector('figcaption')).toHaveTextContent('WB-01 is connected to the broker, which answers it itself.')
  })
})

describe('the configured leader', () => {
  it('is marked on a follower when a failover has moved the charger away from it', () => {
    const configured = backendLink({ key: 'primary', role: 'follower', configured_leader: true, connected: true })
    const now = backendLink({ key: 'standby', role: 'leader', configured_leader: false })
    render(<Topology chargerId="C" mode="relay" backends={[now, configured]} />)
    expect(screen.getByText('not the configured leader')).toBeInTheDocument()
    expect(screen.getByText('configured leader')).toBeInTheDocument()
  })

  it('is not mentioned while the configured leader leads', () => {
    render(<Topology chargerId="C" mode="relay" backends={[backendLink(), follower]} />)
    expect(screen.queryByText('not the configured leader')).not.toBeInTheDocument()
    expect(screen.queryByText('configured leader')).not.toBeInTheDocument()
  })
})
