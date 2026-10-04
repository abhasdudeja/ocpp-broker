import type { AdminChange, AdminOrg, AdminOrgInput } from './api/client'

/** What the broker uses when a setting is left out; a field showing the default is left empty, so it stays unset in the file. */
export const DEFAULTS = { backend_buffer_size: 200, backend_outage_timeout: 30, leader_failover_timeout: 15 } as const
export const DEFAULT_SUBPROTOCOL = 'ocpp1.6'

export type Tri = '' | 'true' | 'false' // empty: the broker's own default

export interface BackendDraft {
  key: number // only to tell rows apart while editing
  id: string
  url: string
  local: boolean
  leader: boolean
  ocpp_subprotocol: string
}

export interface CredentialDraft {
  key: number
  charger_id: string
  /** How the broker holds the password now; null for a charger that is being added */
  stored: 'hash' | 'plaintext' | null
  /** A new password; empty keeps the one the broker has */
  password: string
}

export interface Draft {
  name: string
  connect_to_backend: boolean
  ocpp_subprotocol: string
  backends: BackendDraft[]
  backend_buffer_size: string
  backend_outage_timeout: string
  leader_failover_timeout: string
  mapping: Tri
  follower_wait: string
  dedupe_start: Tri
  retain_closed: string
  retain_open: string
  charger_auth_required: Tri
  credentials: CredentialDraft[]
}

let counter = 0
export const nextKey = (): number => ++counter

const tri = (value: boolean | null | undefined): Tri => (value === null || value === undefined ? '' : value ? 'true' : 'false')
const text = (value: number | null | undefined): string => (value === null || value === undefined ? '' : String(value))
const unlessDefault = (value: number, standard: number): string => (value === standard ? '' : String(value))

export function emptyDraft(): Draft {
  return {
    name: '',
    connect_to_backend: true,
    ocpp_subprotocol: '',
    backends: [],
    backend_buffer_size: '',
    backend_outage_timeout: '',
    leader_failover_timeout: '',
    mapping: '',
    follower_wait: '',
    dedupe_start: '',
    retain_closed: '',
    retain_open: '',
    charger_auth_required: '',
    credentials: [],
  }
}

/** The form for an organization as the broker reads it. */
export function draftFromOrg(org: AdminOrg): Draft {
  return {
    name: org.name,
    connect_to_backend: org.connect_to_backend,
    ocpp_subprotocol: org.ocpp_subprotocol === DEFAULT_SUBPROTOCOL ? '' : org.ocpp_subprotocol,
    backends: org.backends.map((b) => ({
      key: nextKey(),
      id: b.id ?? '',
      url: b.url ?? '',
      local: b.local ?? false,
      leader: b.leader ?? false,
      // A backend inherits the organization's subprotocol; show one only when it differs
      ocpp_subprotocol: b.local || !b.ocpp_subprotocol || b.ocpp_subprotocol === org.ocpp_subprotocol ? '' : b.ocpp_subprotocol,
    })),
    backend_buffer_size: unlessDefault(org.backend_buffer_size, DEFAULTS.backend_buffer_size),
    backend_outage_timeout: unlessDefault(org.backend_outage_timeout, DEFAULTS.backend_outage_timeout),
    leader_failover_timeout: unlessDefault(org.leader_failover_timeout, DEFAULTS.leader_failover_timeout),
    mapping: tri(org.transaction_ids.mapping),
    follower_wait: text(org.transaction_ids.follower_wait),
    dedupe_start: tri(org.transaction_ids.dedupe_start),
    retain_closed: text(org.transaction_ids.retain_closed),
    retain_open: text(org.transaction_ids.retain_open),
    // Shown as chosen only when it differs from what listing credentials implies
    charger_auth_required: org.charger_auth_required === org.credentials.length > 0 ? '' : tri(org.charger_auth_required),
    credentials: org.credentials.map((c) => ({ key: nextKey(), charger_id: c.charger_id, stored: c.storage, password: '' })),
  }
}

const whole = (value: string): number | null => {
  const trimmed = value.trim()
  if (trimmed === '') return null
  const n = Number(trimmed)
  return Number.isFinite(n) ? n : Number.NaN
}

/** Problems with the form that the broker would only report as a rejected request; found before asking. */
export function draftProblems(draft: Draft, isNew: boolean): string[] {
  const problems: string[] = []
  if (isNew && draft.name.trim() === '') problems.push('Give the organization a name.')
  for (const [label, value] of [
    ['Held frames', draft.backend_buffer_size],
    ['Outage timeout', draft.backend_outage_timeout],
    ['Failover timeout', draft.leader_failover_timeout],
  ] as const) {
    const n = whole(value)
    if (n !== null && (!Number.isInteger(n) || n < 0)) problems.push(`${label} must be a whole number, 0 or more, or left empty.`)
  }
  for (const [label, value] of [
    ['Follower wait', draft.follower_wait],
    ['Keep ended transactions', draft.retain_closed],
    ['Keep open transactions', draft.retain_open],
  ] as const) {
    const n = whole(value)
    if (n !== null && !(n > 0)) problems.push(`${label} must be a number of seconds above 0, or left empty.`)
  }
  draft.backends.forEach((b, i) => {
    if (!b.local && b.url.trim() === '') problems.push(`Backend ${i + 1} needs an address, or tick "this broker".`)
  })
  draft.credentials.forEach((c) => {
    if (c.charger_id.trim() === '') problems.push('A credential needs a charger id.')
    else if (c.stored === null && c.password === '') problems.push(`Charger ${c.charger_id.trim()} needs a password.`)
  })
  return problems
}

const orNull = (value: string): string | null => (value.trim() === '' ? null : value.trim())
const boolOrNull = (value: Tri): boolean | null => (value === '' ? null : value === 'true')

/** The request body for an organization form. Empty fields are sent as null, which means "the default". */
export function toInput(draft: Draft): AdminOrgInput {
  return {
    name: draft.name.trim(),
    connect_to_backend: draft.connect_to_backend,
    ocpp_subprotocol: orNull(draft.ocpp_subprotocol),
    backends: draft.backends.map((b) => ({
      id: orNull(b.id),
      url: b.local ? null : orNull(b.url),
      local: b.local,
      leader: b.leader,
      ocpp_subprotocol: b.local ? null : orNull(b.ocpp_subprotocol),
    })),
    backend_buffer_size: whole(draft.backend_buffer_size),
    backend_outage_timeout: whole(draft.backend_outage_timeout),
    leader_failover_timeout: whole(draft.leader_failover_timeout),
    transaction_ids: {
      mapping: boolOrNull(draft.mapping),
      follower_wait: whole(draft.follower_wait),
      dedupe_start: boolOrNull(draft.dedupe_start),
      retain_closed: whole(draft.retain_closed),
      retain_open: whole(draft.retain_open),
    },
    charger_auth_required: boolOrNull(draft.charger_auth_required),
    credentials: draft.credentials.map((c) => ({ charger_id: c.charger_id.trim(), password: c.password === '' ? null : c.password })),
  }
}

export const upsert = (draft: Draft): AdminChange => ({ op: 'upsert', org: toInput(draft) })
export const remove = (name: string): AdminChange => ({ op: 'delete', name })

/** A random password a charger can be given: 24 hex digits (96 bits). */
export function generatePassword(random: (bytes: Uint8Array) => Uint8Array = (bytes) => crypto.getRandomValues(bytes)): string {
  return Array.from(random(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('')
}

/** "Relay to 2 backends", "Answers chargers itself (local), 1 standby"... one line for the list. */
export function describeMode(org: AdminOrg): string {
  const external = org.backends.filter((b) => !b.local).length
  const local = org.backends.some((b) => b.local)
  if (!org.connect_to_backend || org.backends.length === 0) return 'Answers chargers itself'
  if (local) return `Answers chargers itself, ${external} other backend${external === 1 ? '' : 's'}${org.backends.find((b) => b.local)?.leader ? '' : ' (standby)'}`
  return `Relays to ${external} backend${external === 1 ? '' : 's'}`
}
