/**
 * Automated accessibility checks (axe) of every page with data on it. They find what a machine can find in a DOM
 * without a layout engine: missing names and labels, bad roles and ARIA, headings, duplicate ids. They cannot
 * judge colour contrast or keyboard order, which need a person with the page open.
 */
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { describe, expect, it } from 'vitest'

import {
  adminConfig,
  adminOrg,
  backendLink,
  charger,
  detail,
  info,
  mockApi,
  mockBroker,
  org,
  renderApp,
  signedIn,
} from './test-utils'

/** What axe finds wrong in ``root``, one line each. Colour contrast needs real rendering, so it is left to people. */
export async function violationsIn(root: Element = document.body): Promise<string[]> {
  const results = await axe.run(root, { rules: { 'color-contrast': { enabled: false } } })
  return results.violations.map((v) => `${v.id} (${v.impact}): ${v.help} — ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)
}

const status = (over: Record<string, unknown> = {}) => ({
  org: 'Fleet',
  charger_id: 'CP-001',
  connector_id: 1,
  status: 'Charging',
  error_code: 'NoError',
  info: null,
  vendor_id: null,
  vendor_error_code: null,
  timestamp: '2026-10-04T10:00:00Z',
  ...over,
})

const transaction = {
  org: 'Fleet',
  charger_id: 'CP-001',
  transaction_id: 7,
  connector_id: 1,
  id_tag: 'TAG1',
  started_at: '2026-10-04T10:00:00Z',
  stopped_at: '2026-10-04T10:20:00Z',
  meter_start: 1000,
  meter_stop: 2600,
  energy_wh: 1600,
  stop_reason: 'Local',
  stop_id_tag: 'TAG1',
  open: false,
}

const page = (items: unknown[]) => ({ available: true, reason: null, next_cursor: 'c1', items })

async function visit(path: string, ready: () => Promise<unknown>, routes: Record<string, unknown>) {
  signedIn()
  mockApi({ '/api/orgs': [org()], '/api/history/info': { available: true, reason: null, messages_enabled: true, heartbeats_enabled: false, retention_days: {}, counts: {} }, ...routes })
  renderApp(path)
  await ready()
}

describe('accessibility', () => {
  it('sign-in', async () => {
    renderApp('/signin')
    await screen.findByLabelText('API key')
    expect(await violationsIn()).toEqual([])
  })

  it('overview', async () => {
    signedIn()
    mockBroker(info(), [org(), org({ name: 'Home', mode: 'broker', backends: [] })])
    renderApp('/')
    await screen.findByRole('heading', { name: 'Overview' })
    await screen.findByText('Home')
    expect(await violationsIn()).toEqual([])
  })

  it('chargers', async () => {
    await visit('/chargers', () => screen.findByRole('link', { name: 'CP-001' }), { '/api/chargers': { chargers: [charger(), charger({ charger_id: 'CP-002', online: true })], total: 2 } })
    expect(await violationsIn()).toEqual([])
  })

  it('a charger', async () => {
    await visit('/chargers/Fleet/CP-001', () => screen.findByRole('heading', { name: 'Connectors' }), {
      '/api/chargers/Fleet/CP-001': detail({ backends: [backendLink(), backendLink({ key: 'standby', role: 'follower' })] }),
      '/api/history/statuses': page([status()]),
    })
    expect(await violationsIn()).toEqual([])
  })

  it('backends', async () => {
    await visit('/backends', () => screen.findByRole('table', { name: 'Backends of Fleet' }), {
      '/api/backends': [
        {
          org: 'Fleet',
          mode: 'relay',
          chargers: 2,
          backends: [{ key: 'primary', url: 'ws://a.example/ocpp', local: false, configured_leader: true, leading: 2, following: 0, links_up: 2, links_down: 0, buffered_frames: 0, down_chargers: [] }],
        },
      ],
    })
    expect(await violationsIn()).toEqual([])
  })

  it('tags', async () => {
    await visit('/tags', () => screen.findByRole('heading', { name: 'Tags' }), {
      '/api/orgs': [org({ name: 'Home', mode: 'broker', backends: [], transaction_id_mapping: false })],
      '/api/tags/organizations/Home/statistics': { total_tags: 1, active_tags: 1, expired_tags: 0, blocked_tags: 0, tags_by_type: {}, tags_by_status: {} },
      '/api/tags/organizations/Home/tags': { tags: [{ id_tag: 'AAA', status: 'Accepted', tag_type: 'RFID' }], total: 1, limit: 25, offset: 0 },
    })
    await screen.findByRole('row', { name: /AAA/ })
    expect(await violationsIn()).toEqual([])
  })

  it.each([
    ['transactions', '/history', { '/api/history/transactions': page([transaction]) }, 'Transactions, newest first'],
    ['status changes', '/history?tab=statuses', { '/api/history/statuses': page([status(), status({ status: 'Faulted', error_code: 'GroundFailure' })]) }, 'Connector status changes, newest first'],
    [
      'commands',
      '/history?tab=commands',
      { '/api/history/commands': page([{ org: 'Fleet', charger_id: 'CP-001', message_id: 'm', action: 'Reset', payload: { type: 'Soft' }, status: 'success', response: { status: 'Accepted' }, error: null, sent_at: '2026-10-04T10:00:00Z', finished_at: null, duration_ms: 10 }]) },
      'Commands, newest first',
    ],
    [
      'messages',
      '/history?tab=messages',
      { '/api/history/messages': page([{ org: 'Fleet', charger_id: 'CP-001', direction: 'in', type: 'call', action: 'Authorize', message_id: 'a', payload: { idTag: 'T' }, error: null, truncated: false, size: null, timestamp: '2026-10-04T10:00:00Z' }]) },
      'OCPP messages, newest first',
    ],
  ] as const)('history: %s', async (_name, path, routes, caption) => {
    await visit(path, () => screen.findByRole('table', { name: caption }), routes)
    expect(await violationsIn()).toEqual([])
  })

  it('a transaction in the history', async () => {
    await visit('/history/transactions/Fleet/CP-001/7', () => screen.findByRole('img'), {
      '/api/history/transactions/Fleet/CP-001/7': {
        available: true,
        reason: null,
        transaction,
        readings_truncated: false,
        readings: [
          { timestamp: '2026-10-04T10:05:00Z', connector_id: 1, transaction_id: 7, measurand: 'Energy.Active.Import.Register', phase: null, unit: 'Wh', context: null, location: null, value: 1500, raw_value: '1500' },
          { timestamp: '2026-10-04T10:10:00Z', connector_id: 1, transaction_id: 7, measurand: 'Energy.Active.Import.Register', phase: null, unit: 'Wh', context: null, location: null, value: 2100, raw_value: '2100' },
        ],
      },
    })
    expect(await violationsIn()).toEqual([])
  })

  it('admin: organizations and the audit log', async () => {
    await visit('/admin', () => screen.findByRole('table', { name: 'Organizations' }), {
      '/api/admin/config': adminConfig(),
      '/api/admin/audit': { persisted_to: null, entries: [{ time: '2026-10-04T10:00:00Z', action: 'config.apply', outcome: 'applied', source: '10.0.0.1', key_label: 'alice', organizations: ['Fleet'], summary: ['Fleet: x'], detail: null, revision: 'r' }] },
    })
    expect(await violationsIn()).toEqual([])
    await userEvent.click(screen.getByRole('button', { name: 'Audit log' }))
    await screen.findByRole('table', { name: /Changes made through the admin API/ })
    expect(await violationsIn()).toEqual([])
  })

  it('admin: the organization form, with a credential, a backend and a review', async () => {
    await visit('/admin/orgs/Fleet', () => screen.findByRole('form', { name: 'Organization Fleet' }), {
      '/api/admin/config': adminConfig({ organizations: [adminOrg({ credentials: [{ charger_id: 'CP1', storage: 'plaintext' }] })] }),
      '/api/admin/config/validate': { ok: true, errors: [], warnings: ['a warning'], changes: [{ org: 'Fleet', kind: 'changed', lines: ['x → y'] }], conflict: false, current_revision: 'rev-1', connected_chargers: { Fleet: 2 } },
    })
    await userEvent.click(screen.getByText('Relay tuning'))
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    expect(await violationsIn()).toEqual([])
    within(document.body).getByRole('checkbox', { name: /Disconnect those 2 chargers/ })
  })

  it('admin: a new organization form', async () => {
    await visit('/admin/new', () => screen.findByRole('form', { name: 'New organization' }), { '/api/admin/config': adminConfig() })
    await userEvent.click(screen.getByRole('button', { name: 'Add a backend' }))
    await userEvent.click(screen.getByRole('button', { name: 'Add a charger' }))
    expect(await violationsIn()).toEqual([])
  })

  it('finds what is wrong when something is (so a clean result means something)', async () => {
    const host = document.createElement('div')
    host.innerHTML = '<main><img src="x.png"><button></button><input></main>'
    document.body.appendChild(host)
    const found = await violationsIn(host)
    host.remove()
    expect(found.some((v) => v.startsWith('image-alt'))).toBe(true)
    expect(found.some((v) => v.startsWith('button-name'))).toBe(true)
    expect(found.some((v) => v.startsWith('label'))).toBe(true)
  })
})
