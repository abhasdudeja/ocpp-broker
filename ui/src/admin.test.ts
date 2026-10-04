import { describe, expect, it } from 'vitest'

import { describeMode, draftFromOrg, draftProblems, emptyDraft, generatePassword, remove, toInput, upsert, type Draft } from './admin'
import type { AdminOrg } from './api/client'

const tx = { mapping: null, follower_wait: null, dedupe_start: null, retain_closed: null, retain_open: null }

const org = (overrides: Partial<AdminOrg> = {}): AdminOrg => ({
  name: 'Fleet',
  connect_to_backend: true,
  ocpp_subprotocol: 'ocpp1.6',
  backends: [
    { id: 'primary', url: 'ws://a.example/ocpp', leader: true, local: false, ocpp_subprotocol: 'ocpp1.6' },
    { id: 'standby', url: 'ws://b.example/ocpp', leader: false, local: false, ocpp_subprotocol: 'ocpp1.6' },
  ],
  backend_buffer_size: 200,
  backend_outage_timeout: 30,
  leader_failover_timeout: 15,
  transaction_ids: tx,
  charger_auth_required: true,
  credentials: [{ charger_id: 'CP1', storage: 'hash' }],
  tags: 2,
  ...overrides,
})

describe('the form for an organization', () => {
  it('leaves the settings that have their default empty, so they stay unset in the file', () => {
    const draft = draftFromOrg(org())
    expect(draft.ocpp_subprotocol).toBe('')
    expect([draft.backend_buffer_size, draft.backend_outage_timeout, draft.leader_failover_timeout]).toEqual(['', '', ''])
    expect(draft.charger_auth_required).toBe('')
    expect(draft.backends.map((b) => b.ocpp_subprotocol)).toEqual(['', ''])
  })

  it('shows a setting that differs from its default', () => {
    const draft = draftFromOrg(
      org({
        ocpp_subprotocol: 'ocpp2.0.1',
        backend_buffer_size: 50,
        backend_outage_timeout: 0,
        leader_failover_timeout: 0,
        transaction_ids: { mapping: false, follower_wait: 2.5, dedupe_start: true, retain_closed: 60, retain_open: 600 },
      }),
    )
    expect(draft.ocpp_subprotocol).toBe('ocpp2.0.1')
    expect([draft.backend_buffer_size, draft.backend_outage_timeout, draft.leader_failover_timeout]).toEqual(['50', '0', '0'])
    expect([draft.mapping, draft.follower_wait, draft.dedupe_start, draft.retain_closed, draft.retain_open]).toEqual(['false', '2.5', 'true', '60', '600'])
  })

  it('shows a backend subprotocol only when it differs from the organization’s', () => {
    const draft = draftFromOrg(org({ backends: [{ id: 'a', url: 'ws://x', leader: true, local: false, ocpp_subprotocol: 'ocpp2.0.1' }, { id: 'me', url: null, leader: false, local: true, ocpp_subprotocol: null }] }))
    expect(draft.backends.map((b) => b.ocpp_subprotocol)).toEqual(['ocpp2.0.1', ''])
    expect(draft.backends[1]).toMatchObject({ local: true, url: '', leader: false })
  })

  it('shows the sign-in requirement as a choice only when it is not what the credentials imply', () => {
    expect(draftFromOrg(org({ charger_auth_required: false })).charger_auth_required).toBe('false')
    expect(draftFromOrg(org({ charger_auth_required: false, credentials: [] })).charger_auth_required).toBe('')
    expect(draftFromOrg(org({ charger_auth_required: true, credentials: [] })).charger_auth_required).toBe('true')
  })

  it('lists the credentials with how they are stored and no password', () => {
    const draft = draftFromOrg(org({ credentials: [{ charger_id: 'A', storage: 'hash' }, { charger_id: 'B', storage: 'plaintext' }] }))
    expect(draft.credentials.map((c) => [c.charger_id, c.stored, c.password])).toEqual([
      ['A', 'hash', ''],
      ['B', 'plaintext', ''],
    ])
  })

  it('gives every row its own key', () => {
    const draft = draftFromOrg(org())
    const keys = [...draft.backends.map((b) => b.key), ...draft.credentials.map((c) => c.key)]
    expect(new Set(keys).size).toBe(keys.length)
  })
})

describe('the request for a form', () => {
  it('sends nothing for what is empty: the default applies', () => {
    const input = toInput(draftFromOrg(org()))
    expect(input).toMatchObject({
      name: 'Fleet',
      connect_to_backend: true,
      ocpp_subprotocol: null,
      backend_buffer_size: null,
      backend_outage_timeout: null,
      leader_failover_timeout: null,
      charger_auth_required: null,
      transaction_ids: tx,
    })
  })

  it('sends numbers as numbers and trims text', () => {
    const draft: Draft = { ...emptyDraft(), name: '  Newco ', backend_buffer_size: ' 50 ', backend_outage_timeout: '0', follower_wait: '2.5', mapping: 'true', dedupe_start: 'false', charger_auth_required: 'false' }
    expect(toInput(draft)).toMatchObject({
      name: 'Newco',
      backend_buffer_size: 50,
      backend_outage_timeout: 0,
      transaction_ids: { mapping: true, follower_wait: 2.5, dedupe_start: false },
      charger_auth_required: false,
    })
  })

  it('sends a local backend without an address, and a password only when one was typed', () => {
    const draft = draftFromOrg(org({ backends: [{ id: 'me', url: null, leader: true, local: true, ocpp_subprotocol: null }] }))
    draft.backends[0]!.url = 'ws://left-over'
    draft.credentials[0]!.password = ''
    draft.credentials.push({ key: 99, charger_id: ' NEW ', stored: null, password: 'secret-0123456789' })
    const input = toInput(draft)
    expect(input.backends).toEqual([{ id: 'me', url: null, local: true, leader: true, ocpp_subprotocol: null }])
    expect(input.credentials).toEqual([
      { charger_id: 'CP1', password: null },
      { charger_id: 'NEW', password: 'secret-0123456789' },
    ])
  })

  it('makes a change of either kind', () => {
    expect(upsert(emptyDraft())).toMatchObject({ op: 'upsert', org: { connect_to_backend: true } })
    expect(remove('Fleet')).toEqual({ op: 'delete', name: 'Fleet' })
  })
})

describe('what is wrong with a form before it is sent', () => {
  const ok = () => draftFromOrg(org())

  it('has nothing to say about an organization as it is', () => {
    expect(draftProblems(ok(), false)).toEqual([])
  })

  it('wants a name for a new organization only', () => {
    expect(draftProblems({ ...emptyDraft(), name: ' ' }, true)).toEqual(['Give the organization a name.'])
    expect(draftProblems({ ...emptyDraft(), name: ' ' }, false)).toEqual([])
  })

  it.each([
    ['backend_buffer_size', '-1'],
    ['backend_buffer_size', '1.5'],
    ['backend_outage_timeout', 'soon'],
    ['leader_failover_timeout', '-3'],
  ] as const)('refuses %s = %s', (field, value) => {
    const problems = draftProblems({ ...ok(), [field]: value }, false)
    expect(problems).toHaveLength(1)
    expect(problems[0]).toMatch(/whole number, 0 or more/)
  })

  it('accepts 0 for the counts and timeouts', () => {
    expect(draftProblems({ ...ok(), backend_buffer_size: '0', backend_outage_timeout: '0', leader_failover_timeout: '0' }, false)).toEqual([])
  })

  it.each([
    ['follower_wait', '0'],
    ['retain_closed', '-5'],
    ['retain_open', 'x'],
  ] as const)('refuses %s = %s', (field, value) => {
    expect(draftProblems({ ...ok(), [field]: value }, false)[0]).toMatch(/seconds above 0/)
  })

  it('wants an address for each backend that is not this broker', () => {
    const draft = ok()
    draft.backends[1]!.url = ' '
    expect(draftProblems(draft, false)).toEqual(['Backend 2 needs an address, or tick "this broker".'])
    draft.backends[1]!.local = true
    expect(draftProblems(draft, false)).toEqual([])
  })

  it('wants an id for each credential and a password for a new one', () => {
    const draft = ok()
    draft.credentials.push({ key: 1, charger_id: '', stored: null, password: '' })
    draft.credentials.push({ key: 2, charger_id: 'NEW', stored: null, password: '' })
    expect(draftProblems(draft, false)).toEqual(['A credential needs a charger id.', 'Charger NEW needs a password.'])
    draft.credentials[2]!.password = 'a-password-0123'
    expect(draftProblems(draft, false)).toEqual(['A credential needs a charger id.'])
  })
})

describe('passwords made here', () => {
  it('are 24 hex digits from random bytes', () => {
    const password = generatePassword((bytes) => bytes.fill(0xab))
    expect(password).toBe('ab'.repeat(12))
    expect(generatePassword((bytes) => bytes.fill(0x01))).toBe('01'.repeat(12))
  })

  it('differ each time with the real generator', () => {
    const a = generatePassword()
    expect(a).toMatch(/^[0-9a-f]{24}$/)
    expect(generatePassword()).not.toBe(a)
  })
})

describe('the line about how an organization works', () => {
  it('says the broker answers when it connects to nothing', () => {
    expect(describeMode(org({ connect_to_backend: false }))).toBe('Answers chargers itself')
    expect(describeMode(org({ backends: [] }))).toBe('Answers chargers itself')
  })

  it('counts the backends a relay uses', () => {
    expect(describeMode(org())).toBe('Relays to 2 backends')
    expect(describeMode(org({ backends: [org().backends[0]!] }))).toBe('Relays to 1 backend')
  })

  it('says when the broker itself is a backend, leading or on standby', () => {
    const external = org().backends[0]!
    const me = (leader: boolean) => ({ id: 'me', url: null, leader, local: true, ocpp_subprotocol: null })
    expect(describeMode(org({ backends: [me(true), { ...external, leader: false }] }))).toBe('Answers chargers itself, 1 other backend')
    expect(describeMode(org({ backends: [external, me(false)] }))).toBe('Answers chargers itself, 1 other backend (standby)')
  })
})
