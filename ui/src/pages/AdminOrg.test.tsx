import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AdminApplied, AdminPlan } from '../api/client'
import { adminConfig, adminOrg, mockFetch, renderApp, respond, signedIn } from '../test-utils'

const plan = (overrides: Partial<AdminPlan> = {}): AdminPlan => ({
  ok: true,
  errors: [],
  warnings: [],
  changes: [{ org: 'Fleet', kind: 'changed', lines: ['backend_buffer_size: 200 → 50'] }],
  conflict: false,
  current_revision: 'rev-1',
  connected_chargers: {},
  ...overrides,
})

const applied = (overrides: Partial<AdminApplied> = {}): AdminApplied => ({
  revision: 'rev-2',
  backup: 'config.yaml.bak-20261004T100000',
  changes: [{ org: 'Fleet', kind: 'changed', lines: ['backend_buffer_size: 200 → 50'] }],
  warnings: [],
  dropped_connections: 0,
  applied_at: '2026-10-04T10:00:00Z',
  ...overrides,
})

interface Call {
  url: string
  body: Record<string, unknown> | null
}

/** A broker with the admin API on. ``answers`` choose what check and apply say; every call is recorded. */
function broker(options: { config?: unknown; check?: () => Response | unknown; apply?: () => Response | unknown } = {}) {
  const calls: Call[] = []
  const fetch = mockFetch((url, init) => {
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : null
    calls.push({ url, body })
    const answer = (value: unknown) => (value instanceof Response ? value : respond(value))
    if (url.startsWith('/api/admin/config/validate')) return answer(options.check ? options.check() : plan())
    if (url.startsWith('/api/admin/config/apply')) return answer(options.apply ? options.apply() : applied())
    if (url.startsWith('/api/admin/config')) return answer(options.config ?? adminConfig())
    if (url.startsWith('/api/events')) return respond({ detail: 'no stream' }, 404)
    return respond({ detail: `no mock for ${url}` }, 404)
  })
  const sent = (path: string) => calls.filter((c) => c.url.startsWith(path) && c.body !== null)
  return { fetch, calls, sent }
}

async function editing(name = 'Fleet', options: Parameters<typeof broker>[0] = {}) {
  signedIn()
  const server = broker(options)
  renderApp(`/admin/orgs/${name}`)
  await screen.findByRole('form', { name: `Organization ${name}` })
  return server
}

async function adding(options: Parameters<typeof broker>[0] = {}) {
  signedIn()
  const server = broker(options)
  renderApp('/admin/new')
  await screen.findByRole('form', { name: 'New organization' })
  return server
}

afterEach(() => {
  vi.useRealTimers()
})

describe('changing an organization', () => {
  it('starts from what the broker has, with the defaults left empty', async () => {
    await editing()
    expect(screen.getByLabelText('Name')).toHaveValue('Fleet')
    expect(screen.getByLabelText('Name')).toHaveAttribute('readonly')
    expect(screen.getByRole('checkbox', { name: /Connect chargers to backends/ })).toBeChecked()
    expect(screen.getByLabelText('Backend 1 id')).toHaveValue('primary')
    expect(screen.getByLabelText('Backend 1 address')).toHaveValue('ws://a.example/ocpp')
    expect(screen.getByRole('radio', { name: 'Backend 1 leads' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Backend 2 leads' })).not.toBeChecked()
    expect(screen.getByLabelText('Held frames')).toHaveValue('')
    expect(screen.getByLabelText('Chargers must sign in')).toHaveValue('')
    expect(screen.getByLabelText('Charger id of CP1')).toHaveAttribute('readonly')
    expect(screen.getByText('stored as a hash')).toBeInTheDocument()
    expect(screen.getByLabelText('New password for CP1')).toHaveAttribute('placeholder', 'keep the current one')
  })

  it('asks the broker what a change would do, and shows it before anything is applied', async () => {
    const { sent } = await editing()
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))

    const review = within(await screen.findByRole('region', { name: 'What will change' }))
    expect(review.getByText('backend_buffer_size: 200 → 50')).toBeInTheDocument()
    expect(review.getByText('changed')).toBeInTheDocument()
    expect(sent('/api/admin/config/validate')).toHaveLength(1)
    expect(sent('/api/admin/config/apply')).toHaveLength(0)
    const body = sent('/api/admin/config/validate')[0]!.body as { revision: string; changes: Array<{ op: string; org: Record<string, unknown> }> }
    expect(body.revision).toBe('rev-1')
    expect(body.changes).toHaveLength(1)
    expect(body.changes[0]).toMatchObject({ op: 'upsert', org: { name: 'Fleet', backend_buffer_size: 50, backend_outage_timeout: null, ocpp_subprotocol: null, charger_auth_required: null } })
    expect(body.changes[0]!.org.credentials).toEqual([{ charger_id: 'CP1', password: null }])
    expect(screen.getByRole('button', { name: 'Apply changes' })).toBeEnabled()
  })

  it('applies what was checked, with the revision it was based on, and goes back to the list with a notice', async () => {
    const { sent } = await editing()
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    await userEvent.click(screen.getByRole('button', { name: 'Apply changes' }))

    expect(await screen.findByText(/Changes applied/)).toHaveTextContent('Changes applied. The file as it was is kept as config.yaml.bak-20261004T100000.')
    expect(screen.getByRole('heading', { name: 'Admin' })).toBeInTheDocument()
    const request = sent('/api/admin/config/apply')[0]!.body as Record<string, unknown>
    expect(request.revision).toBe('rev-1')
    expect(request.drop_connections).toEqual([])
    expect(request.changes).toEqual(sent('/api/admin/config/validate')[0]!.body!.changes)
  })

  it('does not let a change be applied after the form was edited again', async () => {
    await editing()
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    await userEvent.type(screen.getByLabelText('Outage timeout'), '1')
    expect(screen.queryByRole('region', { name: 'What will change' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled()
  })

  it('shows the errors of a check, and offers no way to apply', async () => {
    await editing('Fleet', { check: () => plan({ ok: false, errors: ['Organization Fleet: only one backend can be local (this broker)'] }) })
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    const review = within(await screen.findByRole('region', { name: 'This cannot be applied' }))
    expect(review.getByRole('alert')).toHaveTextContent('only one backend can be local')
    expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled()
  })

  it('shows the warnings of a check', async () => {
    await editing('Fleet', { check: () => plan({ warnings: ['Fleet: the password for CP2 is stored as plaintext'] }) })
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    expect(within(await screen.findByRole('list', { name: 'Warnings' })).getByText(/stored as plaintext/)).toBeInTheDocument()
  })

  it('says when a check finds nothing to change, and offers no way to apply it', async () => {
    await editing('Fleet', { check: () => plan({ changes: [] }) })
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    expect(await screen.findByText('Nothing differs from the configuration as it is.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled()
  })

  it('offers to reload when the file changed under the page, and loads it again', async () => {
    const { calls } = await editing('Fleet', {
      check: () => plan({ ok: false, conflict: true, current_revision: 'rev-9', errors: ['The configuration file has changed since this page loaded it. Reload, then make the change again.'], changes: [] }),
    })
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    const before = calls.filter((c) => c.url === '/api/admin/config').length
    await userEvent.click(await screen.findByRole('button', { name: 'Reload the configuration' }))
    await waitFor(() => expect(calls.filter((c) => c.url === '/api/admin/config').length).toBeGreaterThan(before))
    await waitFor(() => expect(screen.getByLabelText('Held frames')).toHaveValue('')) // the form starts again from what is there
    expect(screen.queryByRole('region', { name: 'This cannot be applied' })).not.toBeInTheDocument()
  })

  it('tells the reader the file changed while the page was open', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let config = adminConfig()
    signedIn()
    mockFetch((url) => (url.startsWith('/api/events') ? respond({}, 404) : respond(config)))
    renderApp('/admin/orgs/Fleet')
    await screen.findByRole('form', { name: 'Organization Fleet' })
    config = adminConfig({ revision: 'rev-2' })
    await act(() => vi.advanceTimersByTimeAsync(15_000))
    expect(await screen.findByText(/The configuration file has changed since this page loaded it/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Reload' }))
    await waitFor(() => expect(screen.queryByText(/has changed since this page loaded it/)).not.toBeInTheDocument())
  })

  it('shows what the broker says when applying fails, and stays on the page', async () => {
    await editing('Fleet', { apply: () => respond({ detail: 'The configuration file has changed since this page loaded it. Reload, then make the change again.' }, 409) })
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    await userEvent.click(screen.getByRole('button', { name: 'Apply changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('has changed since this page loaded it')
    expect(screen.getByRole('form', { name: 'Organization Fleet' })).toBeInTheDocument()
  })

  it('shows a failure to check', async () => {
    await editing('Fleet', { check: () => respond({ detail: 'boom' }, 500) })
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })

  it('signs out when the broker stops accepting the key while checking', async () => {
    await editing('Fleet', { check: () => respond({ detail: 'no' }, 401) })
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
  })
})

describe('chargers that are connected', () => {
  it('says how many are connected and keep their settings, and offers to disconnect them', async () => {
    const { sent } = await editing('Fleet', { check: () => plan({ connected_chargers: { Fleet: 3 } }), apply: () => applied({ dropped_connections: 3 }) })
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    expect(await screen.findByText(/3 chargers are connected to Fleet now and keep what they connected with until they reconnect/)).toBeInTheDocument()
    const apply = screen.getByRole('button', { name: 'Apply changes' })
    await userEvent.click(screen.getByRole('checkbox', { name: /Disconnect those 3 chargers now/ }))
    expect(screen.getByRole('button', { name: 'Apply changes and disconnect chargers' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Apply changes and disconnect chargers' }))
    expect(await screen.findByText(/Changes applied/)).toHaveTextContent('3 chargers were disconnected and will reconnect')
    expect((sent('/api/admin/config/apply')[0]!.body as { drop_connections: string[] }).drop_connections).toEqual(['Fleet'])
    expect(apply).toBeDefined()
  })

  it('does not offer to disconnect when none are connected', async () => {
    await editing('Fleet', { check: () => plan({ connected_chargers: { Fleet: 0 } }) })
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    expect(screen.queryByRole('checkbox', { name: /Disconnect/ })).not.toBeInTheDocument()
  })

  it('says it in the singular for one charger', async () => {
    await editing('Fleet', { check: () => plan({ connected_chargers: { Fleet: 1 } }) })
    await userEvent.type(screen.getByLabelText('Held frames'), '50')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    expect(await screen.findByText(/1 charger is connected to Fleet now/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /Disconnect those 1 charger now/ })).toBeInTheDocument()
  })
})

describe('adding an organization', () => {
  it('asks for a name before anything can be checked', async () => {
    await adding()
    expect(screen.getByLabelText('Name')).not.toHaveAttribute('readonly')
    expect(screen.getByRole('button', { name: 'Check changes' })).toBeDisabled()
    expect(screen.getByRole('list', { name: 'To fix first' })).toHaveTextContent('Give the organization a name.')
    await userEvent.type(screen.getByLabelText('Name'), 'Newco')
    expect(screen.getByRole('button', { name: 'Check changes' })).toBeEnabled()
    expect(screen.queryByRole('list', { name: 'To fix first' })).not.toBeInTheDocument()
  })

  it('builds the organization from the form, and goes back to the list once it is added', async () => {
    const { sent } = await adding({ check: () => plan({ changes: [{ org: 'Newco', kind: 'added', lines: ['connect_to_backend: true', '+ backend one (ws://x.example/ocpp, leader)'] }] }) })
    await userEvent.type(screen.getByLabelText('Name'), 'Newco')
    await userEvent.click(screen.getByRole('button', { name: 'Add a backend' }))
    await userEvent.type(screen.getByLabelText('Backend 1 id'), 'one')
    await userEvent.type(screen.getByLabelText('Backend 1 address'), 'ws://x.example/ocpp')
    expect(screen.getByRole('radio', { name: 'Backend 1 leads' })).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Add a backend' }))
    await userEvent.click(screen.getByRole('radio', { name: 'Backend 2 leads' }))
    expect(screen.getByRole('radio', { name: 'Backend 1 leads' })).not.toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Remove backend 2' }))
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))

    const review = within(await screen.findByRole('region', { name: 'What will change' }))
    expect(review.getByText('added')).toBeInTheDocument()
    const org = (sent('/api/admin/config/validate')[0]!.body as { changes: Array<{ org: { name: string; backends: unknown[] } }> }).changes[0]!.org
    expect(org.name).toBe('Newco')
    expect(org.backends).toEqual([{ id: 'one', url: 'ws://x.example/ocpp', local: false, leader: false, ocpp_subprotocol: null }])
    await userEvent.click(screen.getByRole('button', { name: 'Add organization' }))
    expect(await screen.findByText(/Changes applied/)).toBeInTheDocument()
  })

  it('asks for the address of a backend that is not this broker, and none for this broker', async () => {
    await adding()
    await userEvent.type(screen.getByLabelText('Name'), 'Newco')
    await userEvent.click(screen.getByRole('button', { name: 'Add a backend' }))
    expect(screen.getByRole('list', { name: 'To fix first' })).toHaveTextContent('Backend 1 needs an address')
    expect(screen.getByRole('button', { name: 'Check changes' })).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Backend 1 is this broker' }))
    expect(screen.getByLabelText('Backend 1 address')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Check changes' })).toBeEnabled()
  })

  it('wants a password for each charger that is added', async () => {
    const { sent } = await adding()
    await userEvent.type(screen.getByLabelText('Name'), 'Newco')
    await userEvent.click(screen.getByRole('button', { name: 'Add a charger' }))
    expect(screen.getByRole('list', { name: 'To fix first' })).toHaveTextContent('A credential needs a charger id')
    await userEvent.type(screen.getByLabelText('Charger id of credential 1'), 'NC1')
    expect(screen.getByRole('list', { name: 'To fix first' })).toHaveTextContent('Charger NC1 needs a password')
    const password = screen.getByLabelText('New password for NC1')
    expect(password).toHaveAttribute('type', 'password')
    await userEvent.type(password, 'a-password-0123456789')
    expect(screen.queryByRole('list', { name: 'To fix first' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    const org = (sent('/api/admin/config/validate')[0]!.body as { changes: Array<{ org: { credentials: unknown[] } }> }).changes[0]!.org
    expect(org.credentials).toEqual([{ charger_id: 'NC1', password: 'a-password-0123456789' }])
  })

  it('generates a password, shows it once to copy, and sends it', async () => {
    const { sent } = await adding()
    await userEvent.type(screen.getByLabelText('Name'), 'Newco')
    await userEvent.click(screen.getByRole('button', { name: 'Add a charger' }))
    await userEvent.type(screen.getByLabelText('Charger id of credential 1'), 'NC1')
    await userEvent.click(screen.getByRole('button', { name: 'Generate a password for NC1' }))
    const field = screen.getByLabelText('New password for NC1') as HTMLInputElement
    expect(field.value).toMatch(/^[0-9a-f]{24}$/)
    expect(field).toHaveAttribute('type', 'text')
    expect(screen.getByText(/Copy it now/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    const org = (sent('/api/admin/config/validate')[0]!.body as { changes: Array<{ org: { credentials: Array<{ password: string }> } }> }).changes[0]!.org
    expect(org.credentials[0]!.password).toBe(field.value)
  })

  it('can take a charger back out before anything is sent', async () => {
    await adding()
    await userEvent.type(screen.getByLabelText('Name'), 'Newco')
    await userEvent.click(screen.getByRole('button', { name: 'Add a charger' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove credential 1' }))
    expect(screen.queryByRole('table', { name: 'Charger credentials' })).not.toBeInTheDocument()
  })
})

describe('credentials of an organization', () => {
  it('shows a password stored as plaintext as one to replace, and sends no password unless one is typed', async () => {
    const config = adminConfig({ organizations: [adminOrg({ credentials: [{ charger_id: 'OLD', storage: 'plaintext' }] })] })
    const { sent } = await editing('Fleet', { config })
    expect(screen.getByText('stored as plaintext').className).toContain('tone-chip-warn')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    const first = (sent('/api/admin/config/validate')[0]!.body as { changes: Array<{ org: { credentials: unknown[] } }> }).changes[0]!.org.credentials
    expect(first).toEqual([{ charger_id: 'OLD', password: null }])
  })

  it('sends a replaced password, and a removed charger is left out', async () => {
    const config = adminConfig({ organizations: [adminOrg({ credentials: [{ charger_id: 'A', storage: 'hash' }, { charger_id: 'B', storage: 'hash' }] })] })
    const { sent } = await editing('Fleet', { config })
    await userEvent.type(screen.getByLabelText('New password for A'), 'rotated-0123456789')
    await userEvent.click(screen.getByRole('button', { name: 'Remove B' }))
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    const credentials = (sent('/api/admin/config/validate')[0]!.body as { changes: Array<{ org: { credentials: unknown[] } }> }).changes[0]!.org.credentials
    expect(credentials).toEqual([{ charger_id: 'A', password: 'rotated-0123456789' }])
  })

  it('does not show a password anywhere on the page', async () => {
    await editing()
    await userEvent.type(screen.getByLabelText('New password for CP1'), 'typed-secret-0123456789')
    expect(document.body.textContent).not.toContain('typed-secret')
  })
})

describe('removing an organization', () => {
  it('asks the broker about the removal, says what goes, and removes it when applied', async () => {
    const { sent } = await editing('Fleet', { check: () => plan({ changes: [{ org: 'Fleet', kind: 'removed', lines: ['organization removed with its 1 credential(s) and 2 tag(s) in the file'] }] }) })
    await userEvent.click(screen.getByRole('button', { name: 'Remove organization…' }))
    expect(screen.getByRole('heading', { name: 'Remove Fleet' })).toBeInTheDocument()
    expect(screen.getByText(/1 credential\(s\) and the 2 tag\(s\)/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Held frames')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    expect(sent('/api/admin/config/validate')[0]!.body!.changes).toEqual([{ op: 'delete', name: 'Fleet' }])
    await userEvent.click(screen.getByRole('button', { name: 'Remove Fleet' }))
    expect(await screen.findByText(/Changes applied/)).toBeInTheDocument()
    expect(sent('/api/admin/config/apply')[0]!.body!.changes).toEqual([{ op: 'delete', name: 'Fleet' }])
  })

  it('can be called off', async () => {
    await editing()
    await userEvent.click(screen.getByRole('button', { name: 'Remove organization…' }))
    await userEvent.click(screen.getByRole('button', { name: 'Keep it' }))
    expect(screen.getByLabelText('Held frames')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Remove Fleet' })).not.toBeInTheDocument()
  })

  it('is not offered for an organization that is being added', async () => {
    await adding()
    expect(screen.queryByRole('button', { name: 'Remove organization…' })).not.toBeInTheDocument()
  })
})

describe('when it cannot be done', () => {
  it('explains that the admin API is off', async () => {
    signedIn()
    broker({ config: respond({ detail: 'disabled' }, 403) })
    renderApp('/admin/orgs/Fleet')
    expect(await screen.findByText(/switched off/)).toBeInTheDocument()
  })

  it('says there is no such organization', async () => {
    signedIn()
    broker()
    renderApp('/admin/orgs/Nowhere')
    expect(await screen.findByText(/There is no organization named Nowhere/)).toBeInTheDocument()
  })

  it('shows why a file that cannot be written cannot be changed, and offers no form', async () => {
    signedIn()
    broker({ config: adminConfig({ writable: false, writable_reason: 'The configuration file or its folder is read-only for the broker' }) })
    renderApp('/admin/orgs/Fleet')
    expect(await screen.findByRole('alert')).toHaveTextContent('read-only for the broker')
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
  })

  it('shows a failure to load', async () => {
    signedIn()
    broker({ config: respond({ detail: 'boom' }, 500) })
    renderApp('/admin/orgs/Fleet')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load: boom')
  })

  it('asks for an organization whose name needs encoding as it is', async () => {
    signedIn()
    const { calls } = broker({ config: adminConfig({ organizations: [adminOrg({ name: 'My Org' })] }) })
    renderApp('/admin/orgs/My%20Org')
    await screen.findByRole('form', { name: 'Organization My Org' })
    expect(calls.some((c) => c.url === '/api/admin/config')).toBe(true)
  })
})

describe('the relay tuning', () => {
  it('sends the tuning that was typed and refuses what is not a number before asking the broker', async () => {
    const { sent } = await editing()
    await userEvent.click(screen.getByText('Relay tuning'))
    await userEvent.type(screen.getByLabelText('Outage timeout'), 'soon')
    expect(screen.getByRole('list', { name: 'To fix first' })).toHaveTextContent('Outage timeout must be a whole number')
    expect(screen.getByRole('button', { name: 'Check changes' })).toBeDisabled()
    await userEvent.clear(screen.getByLabelText('Outage timeout'))
    await userEvent.type(screen.getByLabelText('Outage timeout'), '45')
    await userEvent.type(screen.getByLabelText('Failover timeout'), '0')
    await userEvent.selectOptions(screen.getByLabelText('Transaction id mapping'), 'false')
    await userEvent.type(screen.getByLabelText('Keep open transactions'), '600')
    await userEvent.click(screen.getByRole('button', { name: 'Check changes' }))
    await screen.findByRole('region', { name: 'What will change' })
    const org = (sent('/api/admin/config/validate')[0]!.body as { changes: Array<{ org: Record<string, unknown> }> }).changes[0]!.org
    expect(org).toMatchObject({ backend_outage_timeout: 45, leader_failover_timeout: 0, transaction_ids: { mapping: false, retain_open: 600 } })
  })
})
