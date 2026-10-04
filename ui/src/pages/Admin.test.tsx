import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { AuditEntry } from '../api/client'
import { adminConfig, adminOrg, mockApi, renderApp, respond, signedIn } from '../test-utils'

const entry = (overrides: Partial<AuditEntry> = {}): AuditEntry => ({
  time: '2026-10-04T10:00:00Z',
  action: 'config.apply',
  outcome: 'applied',
  source: '10.0.0.7',
  key_label: 'alice',
  organizations: ['Fleet'],
  summary: ['Fleet: backend_buffer_size: 200 → 50'],
  detail: null,
  revision: 'rev-2',
  ...overrides,
})

function open(routes: Record<string, unknown>, path = '/admin') {
  signedIn()
  mockApi(routes)
  renderApp(path)
}

describe('the admin page', () => {
  it('lists the organizations with how they work, their backends and their chargers', async () => {
    open({ '/api/admin/config': adminConfig() })
    const table = within(await screen.findByRole('table', { name: 'Organizations' }))
    const fleet = within(table.getByRole('row', { name: /Fleet/ }))
    expect(fleet.getByRole('link', { name: 'Fleet' })).toHaveAttribute('href', '/admin/orgs/Fleet')
    expect(fleet.getByText('Relays to 2 backends')).toBeInTheDocument()
    expect(fleet.getByText('primary')).toBeInTheDocument()
    expect(fleet.getByText('leader')).toBeInTheDocument()
    expect(fleet.getByText('chargers must sign in')).toBeInTheDocument()
    expect(fleet.getByText('1 credential')).toBeInTheDocument()
    const home = within(table.getByRole('row', { name: /Home/ }))
    expect(home.getByText('Answers chargers itself')).toBeInTheDocument()
    expect(home.getByText('none')).toBeInTheDocument()
    expect(home.getByText('open to any charger').className).toContain('tone-chip-warn')
    expect(home.getByText('0 credentials')).toBeInTheDocument()
  })

  it('warns about passwords stored as plaintext', async () => {
    open({ '/api/admin/config': adminConfig({ organizations: [adminOrg({ credentials: [{ charger_id: 'A', storage: 'plaintext' }, { charger_id: 'B', storage: 'hash' }] })] }) })
    expect(await screen.findByText('2 credentials · 1 stored as plaintext')).toBeInTheDocument()
  })

  it('offers adding an organization, and says saving rewrites the file without its comments', async () => {
    open({ '/api/admin/config': adminConfig() })
    expect(await screen.findByRole('link', { name: 'Add organization' })).toHaveAttribute('href', '/admin/new')
    expect(screen.getByText(/comments are not kept/)).toBeInTheDocument()
    expect(screen.getByText('/etc/ocpp-broker/config.yaml')).toBeInTheDocument()
    expect(screen.getByText(/the latest 10/)).toBeInTheDocument()
  })

  it('says the organizations are in MongoDB, and not that a file is rewritten', async () => {
    open({ '/api/admin/config': adminConfig({ store: 'mongodb', path: 'MongoDB: collection config_organizations in database ocpp' }) })
    expect(await screen.findByText(/kept in MongoDB/)).toHaveTextContent('shared by every broker instance')
    expect(screen.getByText('MongoDB: collection config_organizations in database ocpp')).toBeInTheDocument()
    expect(screen.queryByText(/comments are not kept/)).not.toBeInTheDocument()
  })

  it('says why nothing can be changed when the file cannot be written, and offers no Add', async () => {
    open({ '/api/admin/config': adminConfig({ writable: false, writable_reason: 'The configuration file or its folder is read-only for the broker' }) })
    expect(await screen.findByRole('alert')).toHaveTextContent('read-only for the broker')
    expect(screen.queryByRole('link', { name: 'Add organization' })).not.toBeInTheDocument()
  })

  it('says when no organization is configured', async () => {
    open({ '/api/admin/config': adminConfig({ organizations: [] }) })
    expect(await screen.findByText(/No organization is configured/)).toBeInTheDocument()
  })

  it('explains how to switch the admin API on when it is off', async () => {
    open({ '/api/admin/config': respond({ detail: 'The admin API is disabled' }, 403) })
    expect(await screen.findByText(/switched off/)).toBeInTheDocument()
    expect(screen.getByText(/enabled: true/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows another failure as an error', async () => {
    open({ '/api/admin/config': respond({ detail: 'boom' }, 500) })
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load: boom')
  })

  it('signs out when the broker stops accepting the key', async () => {
    open({ '/api/admin/config': respond({ detail: 'no' }, 401) })
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
  })

  it('has Admin in the navigation', async () => {
    open({ '/api/admin/config': adminConfig() })
    expect(await screen.findByRole('link', { name: 'Admin' })).toHaveAttribute('href', '/admin')
  })
})

describe('the audit log', () => {
  it('lists what was changed, by whom and from where', async () => {
    open({
      '/api/admin/config': adminConfig(),
      '/api/admin/audit': {
        persisted_to: '/var/log/audit.jsonl',
        entries: [entry(), entry({ outcome: 'refused', key_label: null, source: null, organizations: [], summary: [], detail: 'The configuration file has changed since this page loaded it.' })],
      },
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Audit log' }))
    const table = within(await screen.findByRole('table', { name: /Changes made through the admin API/ }))
    const rows = table.getAllByRole('row').slice(1)
    expect(within(rows[0]!).getByText('applied').className).toContain('tone-chip-ok')
    expect(within(rows[0]!).getByText('alice')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('10.0.0.7')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('Fleet: backend_buffer_size: 200 → 50')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('refused').className).toContain('tone-chip-warn')
    expect(within(rows[1]!).getByText('unknown key')).toBeInTheDocument()
    expect(within(rows[1]!).getByText(/has changed since this page loaded it/)).toBeInTheDocument()
    expect(screen.getByText('/var/log/audit.jsonl')).toBeInTheDocument()
  })

  it('marks a failed change', async () => {
    open({ '/api/admin/config': adminConfig(), '/api/admin/audit': { persisted_to: null, entries: [entry({ outcome: 'failed' })] } })
    await userEvent.click(await screen.findByRole('button', { name: 'Audit log' }))
    expect((await screen.findByText('failed')).className).toContain('tone-chip-bad')
  })

  it('says when nothing has been changed yet', async () => {
    open({ '/api/admin/config': adminConfig(), '/api/admin/audit': { persisted_to: null, entries: [] } })
    await userEvent.click(await screen.findByRole('button', { name: 'Audit log' }))
    expect(await screen.findByText(/Nothing has been changed through the admin API yet/)).toBeInTheDocument()
  })

  it('shows a failure to load it', async () => {
    open({ '/api/admin/config': adminConfig(), '/api/admin/audit': respond({ detail: 'boom' }, 500) })
    await userEvent.click(await screen.findByRole('button', { name: 'Audit log' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load: boom')
  })

  it('is addressed as a tab and leaves out the Add button', async () => {
    open({ '/api/admin/config': adminConfig(), '/api/admin/audit': { persisted_to: null, entries: [] } }, '/admin?tab=audit')
    expect(await screen.findByText(/Nothing has been changed/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Audit log' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('link', { name: 'Add organization' })).not.toBeInTheDocument()
  })
})
