import type { BrokerEvent } from './api/client'
import type { Tone } from './components/Chip'
import { formatUptime } from './format'

const text = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null)
const count = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

function connector(id: unknown): string {
  const n = count(id)
  return n === null ? 'a connector' : n === 0 ? 'the charger' : `connector ${n}`
}

/** One line saying what happened, written for a person. Fields the broker did not send are left out. */
export function describeEvent(event: BrokerEvent): string {
  const d = event.data
  switch (event.type) {
    case 'charger.connected': {
      const how = [text(d.mode), text(d.ocpp_version) && `OCPP ${text(d.ocpp_version)}`].filter(Boolean).join(', ')
      const from = text(d.remote_address)
      return `connected${how ? ` (${how})` : ''}${from ? ` from ${from}` : ''}`
    }
    case 'charger.disconnected': {
      const seconds = count(d.connected_for_seconds)
      return seconds === null ? 'disconnected' : `disconnected after ${formatUptime(seconds)}`
    }
    case 'charger.replaced':
      return 'connected again; the earlier connection was closed'
    case 'charger.boot': {
      const who = [text(d.vendor), text(d.model)].filter(Boolean).join(' ')
      const firmware = text(d.firmware_version)
      return `booted${who ? `: ${who}` : ''}${firmware ? `, firmware ${firmware}` : ''}`
    }
    case 'charger.status': {
      const to = text(d.status) ?? 'unknown'
      const from = text(d.previous)
      const error = text(d.error_code)
      return `${connector(d.connector_id)}: ${from ? `${from} → ${to}` : to}${error && error !== 'NoError' ? ` (${error})` : ''}`
    }
    case 'backend.link': {
      const name = text(d.backend) ?? 'a backend'
      const role = text(d.role)
      return `${name}${role ? ` (${role})` : ''} ${d.connected === true ? 'is connected' : 'was lost'}`
    }
    case 'backend.failover': {
      const reason = text(d.reason)
      const to = text(d.new_leader) ?? 'a follower'
      const from = text(d.old_leader) ?? 'the leader'
      if (reason === 'failback') return `fail-back: ${to} has the charger again, from ${from}`
      if (reason === 'manual') return `leader changed by an operator: ${to} took over from ${from}`
      return `failover: ${to} took over from ${from}`
    }
    case 'transaction.started': {
      const meter = count(d.meter_start)
      const id = text(d.transaction_id) // known when the charger chose it (OCPP 2.x)
      return `transaction${id === null ? '' : ` ${id}`} started on ${connector(d.connector_id)}${meter === null ? '' : ` (meter ${meter} Wh)`}`
    }
    case 'transaction.stopped': {
      const id = count(d.transaction_id) ?? text(d.transaction_id) // a number in OCPP 1.6, text in 2.x
      const bits = [text(d.reason) && `reason ${text(d.reason)}`, count(d.meter_stop) !== null && `meter ${count(d.meter_stop)} Wh`].filter(Boolean)
      return `transaction${id === null ? '' : ` ${id}`} stopped${bits.length ? ` (${bits.join(', ')})` : ''}`
    }
    case 'command.result': {
      const error = text(d.error)
      return `${text(d.action) ?? 'command'}: ${text(d.status) ?? 'finished'}${error ? ` — ${error}` : ''}`
    }
    default:
      return event.type
  }
}

/** Colour of an event's marker; the words carry the meaning. */
export function eventTone(event: BrokerEvent): Tone {
  const d = event.data
  switch (event.type) {
    case 'charger.connected':
      return 'ok'
    case 'backend.failover':
      return d.reason === 'failover' || d.reason === undefined ? 'bad' : 'info'
    case 'charger.status':
      return d.status === 'Faulted' ? 'bad' : d.status === 'Unavailable' ? 'warn' : 'neutral'
    case 'backend.link':
      return d.connected === true ? 'ok' : 'warn'
    case 'command.result':
      return d.status === 'success' ? 'ok' : 'warn'
    case 'charger.disconnected':
    case 'charger.replaced':
      return 'warn'
    default:
      return 'info'
  }
}
