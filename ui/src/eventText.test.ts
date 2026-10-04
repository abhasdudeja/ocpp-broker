import { describe, expect, it } from 'vitest'

import { describeEvent, eventTone } from './eventText'
import { brokerEvent } from './test-utils'

type Type = Parameters<typeof brokerEvent>[0]
const text = (type: Type, data: Record<string, unknown>) => describeEvent(brokerEvent(type, data))
const tone = (type: Type, data: Record<string, unknown> = {}) => eventTone(brokerEvent(type, data))

describe('describeEvent', () => {
  it('says how and from where a charger connected', () => {
    expect(text('charger.connected', { mode: 'relay', ocpp_version: '1.6', remote_address: '10.0.0.5:51234' })).toBe('connected (relay, OCPP 1.6) from 10.0.0.5:51234')
    expect(text('charger.connected', {})).toBe('connected')
  })

  it('says how long a charger had been connected', () => {
    expect(text('charger.disconnected', { connected_for_seconds: 303 })).toBe('disconnected after 5m 3s')
    expect(text('charger.disconnected', {})).toBe('disconnected')
  })

  it('says a charger came back on a new connection', () => {
    expect(text('charger.replaced', {})).toBe('connected again; the earlier connection was closed')
  })

  it('names what booted, with the firmware if it was given', () => {
    expect(text('charger.boot', { vendor: 'Acme', model: 'Wallbox 7', firmware_version: '2.1.0' })).toBe('booted: Acme Wallbox 7, firmware 2.1.0')
    expect(text('charger.boot', { vendor: 'Acme', model: null, firmware_version: null })).toBe('booted: Acme')
    expect(text('charger.boot', {})).toBe('booted')
  })

  it('describes a connector status change, and the error that came with it', () => {
    expect(text('charger.status', { connector_id: 1, status: 'Charging', previous: 'Preparing', error_code: 'NoError' })).toBe('connector 1: Preparing → Charging')
    expect(text('charger.status', { connector_id: 2, status: 'Faulted', previous: null, error_code: 'GroundFailure' })).toBe('connector 2: Faulted (GroundFailure)')
    expect(text('charger.status', { connector_id: 0, status: 'Available', previous: 'Unavailable' })).toBe('the charger: Unavailable → Available')
    expect(text('charger.status', { status: 'Available' })).toBe('a connector: Available')
  })

  it('says which backend went up or down and in what role', () => {
    expect(text('backend.link', { backend: 'primary', role: 'leader', connected: false })).toBe('primary (leader) was lost')
    expect(text('backend.link', { backend: 'standby', role: 'follower', connected: true })).toBe('standby (follower) is connected')
  })

  it('says who took over from whom in a failover', () => {
    expect(text('backend.failover', { old_leader: 'primary', new_leader: 'standby' })).toBe('failover: standby took over from primary')
    expect(text('backend.failover', { old_leader: 'primary', new_leader: 'standby', reason: 'failover' })).toBe('failover: standby took over from primary')
  })

  it('tells a fail-back and an operator’s change from a failover', () => {
    expect(text('backend.failover', { old_leader: 'standby', new_leader: 'primary', reason: 'failback' })).toBe('fail-back: primary has the charger again, from standby')
    expect(text('backend.failover', { old_leader: 'primary', new_leader: 'standby', reason: 'manual' })).toBe('leader changed by an operator: standby took over from primary')
  })

  it('describes transactions', () => {
    expect(text('transaction.started', { connector_id: 1, meter_start: 100 })).toBe('transaction started on connector 1 (meter 100 Wh)')
    expect(text('transaction.started', { connector_id: null, meter_start: null })).toBe('transaction started on a connector')
    expect(text('transaction.stopped', { transaction_id: 7, reason: 'Local', meter_stop: 4100 })).toBe('transaction 7 stopped (reason Local, meter 4100 Wh)')
    expect(text('transaction.stopped', { transaction_id: null, reason: null, meter_stop: null })).toBe('transaction stopped')
  })

  it('describes a command and how it ended', () => {
    expect(text('command.result', { action: 'Reset', status: 'success', error: null })).toBe('Reset: success')
    expect(text('command.result', { action: 'Reset', status: 'timeout', error: 'No response within 30s' })).toBe('Reset: timeout — No response within 30s')
  })

  it('shows the type of an event it does not know instead of nothing', () => {
    expect(describeEvent({ ...brokerEvent('charger.status'), type: 'something.new' as never })).toBe('something.new')
  })

  it('is not thrown by values of the wrong type', () => {
    expect(text('charger.status', { connector_id: 'one', status: 5, previous: {}, error_code: [] })).toBe('a connector: unknown')
    expect(text('charger.disconnected', { connected_for_seconds: 'long' })).toBe('disconnected')
  })
})

describe('eventTone', () => {
  it('marks arrivals green, departures and lost links amber, and a failover or a fault red', () => {
    expect(tone('charger.connected')).toBe('ok')
    expect(tone('charger.disconnected')).toBe('warn')
    expect(tone('backend.link', { connected: true })).toBe('ok')
    expect(tone('backend.link', { connected: false })).toBe('warn')
    expect(tone('backend.failover')).toBe('bad')
    expect(tone('backend.failover', { reason: 'failover' })).toBe('bad')
    expect(tone('backend.failover', { reason: 'failback' })).toBe('info')
    expect(tone('backend.failover', { reason: 'manual' })).toBe('info')
    expect(tone('charger.status', { status: 'Faulted' })).toBe('bad')
    expect(tone('charger.status', { status: 'Unavailable' })).toBe('warn')
    expect(tone('charger.status', { status: 'Charging' })).toBe('neutral')
    expect(tone('command.result', { status: 'success' })).toBe('ok')
    expect(tone('command.result', { status: 'error' })).toBe('warn')
  })
})
