import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Tag } from '../api/client'
import { mockFetch, org, renderApp, respond, signedIn } from '../test-utils'

const tag = (id: string, overrides: Partial<Tag> = {}): Tag => ({ id_tag: id, status: 'Accepted', tag_type: 'RFID', ...overrides })

interface Call {
  method: string
  url: string
  body: Record<string, unknown> | null
}

/** A broker's tag API in memory: enough of the real rules for the page to be exercised against. */
function fakeTags(initial: Tag[] = [], options: { orgs?: ReturnType<typeof org>[] } = {}) {
  const tags = new Map(initial.map((t) => [t.id_tag, t]))
  const calls: Call[] = []
  const state = { syncFails: false, importError: null as string | null, failBulkFor: new Set<string>() }
  const orgs = options.orgs ?? [org({ name: 'Home', mode: 'broker', backends: [], transaction_id_mapping: false }), org({ name: 'Fleet' })]
  const base = '/api/tags/organizations/Home'

  const fetch = mockFetch((url, init) => {
    const method = init?.method ?? 'GET'
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null
    calls.push({ method, url, body })
    const path = url.split('?')[0] ?? url
    const query = new URLSearchParams(url.split('?')[1] ?? '')

    if (path === '/api/orgs') return respond(orgs)
    if (path === `${base}/statistics`) {
      const all = [...tags.values()]
      return respond({
        total_tags: all.length,
        active_tags: all.filter((t) => t.status === 'Accepted').length,
        expired_tags: all.filter((t) => t.status === 'Expired').length,
        blocked_tags: all.filter((t) => t.status === 'Blocked').length,
        tags_by_type: {},
        tags_by_status: {},
      })
    }
    if (path === `${base}/tags` && method === 'GET') {
      const needle = query.get('id_tag')?.toLowerCase()
      const found = [...tags.values()].filter(
        (t) => (!needle || t.id_tag.toLowerCase().includes(needle)) && (!query.get('status') || t.status === query.get('status')) && (!query.get('tag_type') || t.tag_type === query.get('tag_type')),
      )
      const limit = Number(query.get('limit') ?? 100)
      const offset = Number(query.get('offset') ?? 0)
      return respond({ tags: found.slice(offset, offset + limit), total: found.length, limit, offset })
    }
    if (path === `${base}/tags/validate`) {
      const t = body as unknown as Tag
      const errors: string[] = []
      if (!query.get('for_update') && tags.has(t.id_tag)) errors.push(`Tag ${t.id_tag} already exists`)
      if (t.id_tag.length > 20) errors.push('id_tag is longer than 20 characters')
      const warnings = t.expiry_date && new Date(t.expiry_date).getTime() < Date.now() ? ['The expiry date is in the past'] : []
      return respond({ is_valid: errors.length === 0, errors, warnings })
    }
    if (path === `${base}/tags` && method === 'POST') {
      const t = body as unknown as Tag
      tags.set(t.id_tag, t)
      return respond({ success: true, message: `Tag ${t.id_tag} added successfully` })
    }
    if (path.startsWith(`${base}/tags/`) && method === 'PUT') {
      const t = body as unknown as Tag
      tags.set(t.id_tag, t)
      return respond({ success: true, message: `Tag ${t.id_tag} updated successfully` })
    }
    if (path.startsWith(`${base}/tags/`) && method === 'DELETE') {
      const id = decodeURIComponent(path.slice(`${base}/tags/`.length))
      tags.delete(id)
      return respond({ success: true, message: `Tag ${id} deleted successfully` })
    }
    if (path === `${base}/tags/bulk`) {
      const list = (body?.tags ?? []) as Tag[]
      const results = list.map((t) => {
        if (state.failBulkFor.has(t.id_tag)) return { id_tag: t.id_tag, success: false, error: 'locked' }
        if (body?.operation === 'delete') tags.delete(t.id_tag)
        else tags.set(t.id_tag, t)
        return { id_tag: t.id_tag, success: true, error: null }
      })
      const succeeded = results.filter((r) => r.success).length
      return respond({ operation: body?.operation, total: results.length, succeeded, failed: results.length - succeeded, results })
    }
    if (path === `${base}/tags/import`) {
      if (state.importError) return respond({ detail: state.importError }, 400)
      const records = JSON.parse(String(body?.data)) as Tag[]
      const fresh = records.filter((r) => !tags.has(r.id_tag))
      const existing = records.filter((r) => tags.has(r.id_tag))
      const bad = records.filter((r) => r.id_tag.length > 20)
      const result = {
        source: 'json',
        validate_only: body?.validate_only,
        total: records.length,
        imported: fresh.length - bad.length,
        updated: body?.overwrite_existing ? existing.length : 0,
        skipped: body?.overwrite_existing ? 0 : existing.length,
        errors: bad.map((r, i) => ({ record: records.indexOf(r) + 1 + i * 0, id_tag: r.id_tag, error: 'id_tag is longer than 20 characters' })),
      }
      if (!body?.validate_only) for (const r of fresh) if (r.id_tag.length <= 20) tags.set(r.id_tag, r)
      return respond(result)
    }
    if (path === `${base}/tags/export`) {
      const all = [...tags.values()]
      return new Response(body?.format === 'csv' ? `id_tag,status\n${all.map((t) => `${t.id_tag},${t.status}`).join('\n')}\n` : JSON.stringify(all), { status: 200 })
    }
    if (path === '/api/tags/sync') {
      if (state.syncFails) return respond({ detail: 'MongoDB is not connected' }, 503)
      return respond({ success: true, message: 'Synced', organizations: { Home: { loaded: 3, seeded: 0, dropped: 1 } } })
    }
    return respond({ detail: `no mock for ${method} ${url}` }, 404)
  })
  const sent = (method: string, suffix: string) => calls.filter((c) => c.method === method && c.url.split('?')[0]?.endsWith(suffix))
  return { tags, calls, state, fetch, sent }
}

async function open(path = '/tags') {
  signedIn()
  renderApp(path)
  await screen.findByRole('heading', { name: 'Tags' })
}

const row = (id: string) => within(screen.getByRole('row', { name: new RegExp(`^.*${id}`) }))

beforeEach(() => {
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:fake'), revokeObjectURL: vi.fn() }))
})
afterEach(() => vi.restoreAllMocks())

describe('the tags page', () => {
  it('lists the tags of an organization where the broker answers chargers, with their status, type and expiry', async () => {
    fakeTags([tag('AAA', { description: 'Ann', parent_id_tag: 'ROOT' }), tag('BBB', { status: 'Blocked', tag_type: 'NFC', expiry_date: '2030-01-31T00:00:00Z' })])
    await open()
    await screen.findByRole('row', { name: /AAA/ })
    expect(within(screen.getByLabelText('Organization')).getAllByRole('option').map((o) => o.textContent)).toEqual(['Home'])
    expect(row('AAA').getByText('Accepted').className).toContain('tone-chip-ok')
    expect(row('AAA').getByText('Ann')).toBeInTheDocument()
    expect(row('AAA').getByText('ROOT')).toBeInTheDocument()
    expect(row('AAA').getByText('never')).toBeInTheDocument()
    expect(row('BBB').getByText('Blocked').className).toContain('tone-chip-bad')
    expect(row('BBB').getByText('NFC')).toBeInTheDocument()
    expect(row('BBB').queryByText('never')).not.toBeInTheDocument()
  })

  it('shows how many tags there are, how many are active, expired and blocked', async () => {
    fakeTags([tag('A'), tag('B'), tag('C', { status: 'Blocked' }), tag('D', { status: 'Expired' })])
    await open()
    const facts = await screen.findByText('Active')
    const group = facts.closest('dl') as HTMLElement
    expect(within(group).getByText('Tags').nextSibling).toHaveTextContent('4')
    expect(within(group).getByText('Active').nextSibling).toHaveTextContent('2')
    expect(within(group).getByText('Expired').nextSibling).toHaveTextContent('1')
    expect(within(group).getByText('Blocked').nextSibling).toHaveTextContent('1')
  })

  it('explains when no organization lets the broker answer chargers', async () => {
    fakeTags([], { orgs: [org({ name: 'Fleet' })] })
    await open()
    expect(await screen.findByText(/No organization lets the broker answer chargers itself/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add tag' })).not.toBeInTheDocument()
  })

  it('offers a local leader organization too', async () => {
    fakeTags([], { orgs: [org({ name: 'Hybrid', mode: 'broker' }), org({ name: 'Home', mode: 'broker', backends: [] })] })
    await open()
    await screen.findByLabelText('Organization')
    expect(within(screen.getByLabelText('Organization')).getAllByRole('option').map((o) => o.textContent)).toEqual(['Hybrid', 'Home'])
  })

  it('says so when the organization has no tags', async () => {
    fakeTags([])
    await open()
    expect(await screen.findByText(/has no tags yet/)).toBeInTheDocument()
  })

  it('says no tag matches when a search finds nothing', async () => {
    fakeTags([tag('AAA')])
    await open('/tags?q=zzz')
    expect(await screen.findByText('No tag matches.')).toBeInTheDocument()
  })

  it('asks the broker to filter and keeps the filters in the address', async () => {
    const { calls } = fakeTags([tag('AAA'), tag('BBB', { status: 'Blocked', tag_type: 'NFC' })])
    await open('/tags?status=Blocked&type=NFC&q=b')
    await screen.findByRole('row', { name: /BBB/ })
    expect(screen.queryByRole('row', { name: /AAA/ })).not.toBeInTheDocument()
    const search = calls.find((c) => c.url.includes('/tags?'))
    expect(search?.url).toContain('id_tag=b')
    expect(search?.url).toContain('status=Blocked')
    expect(search?.url).toContain('tag_type=NFC')
    expect(screen.getByLabelText('Status')).toHaveValue('Blocked')
    expect(screen.getByLabelText('Type')).toHaveValue('NFC')
    expect(screen.getByLabelText('Search tags')).toHaveValue('b')

    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Status'), '')
    await waitFor(() => expect(calls.filter((c) => c.url.includes('/tags?')).at(-1)?.url).not.toContain('status='))
    expect(screen.getByLabelText('Type')).toHaveValue('NFC')
  })

  it('pages through a long list', async () => {
    fakeTags(Array.from({ length: 60 }, (_, n) => tag(`T${String(n).padStart(2, '0')}`)))
    await open()
    await screen.findByRole('row', { name: /T00/ })
    expect(screen.getByText('1–25 of 60')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByRole('row', { name: /T25/ })
    expect(screen.getByText('26–50 of 60')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByRole('row', { name: /T50/ })
    expect(screen.getByText('51–60 of 60')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Previous' }))
    await screen.findByRole('row', { name: /T25/ })
  })

  it('has no pager when everything fits on one page', async () => {
    fakeTags([tag('AAA')])
    await open()
    await screen.findByRole('row', { name: /AAA/ })
    expect(screen.queryByRole('navigation', { name: 'Pages' })).not.toBeInTheDocument()
  })

  it('goes back to the first page when a filter changes', async () => {
    const { calls } = fakeTags(Array.from({ length: 60 }, (_, n) => tag(`T${String(n).padStart(2, '0')}`)))
    await open('/tags?offset=25')
    await screen.findByRole('row', { name: /T25/ })
    await userEvent.setup().type(screen.getByLabelText('Search tags'), 'T0')
    await waitFor(() => expect(calls.some((c) => c.url.includes('id_tag=T0') && !c.url.includes('offset'))).toBe(true))
  })

  it('reports a failed first load, and signs out when the key is refused', async () => {
    mockFetch((url) => (url === '/api/orgs' ? respond([org({ name: 'Home', mode: 'broker', backends: [] })]) : respond({ detail: 'database on fire' }, 500)))
    await open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load: database on fire')
  })

  it('signs out when the broker stops accepting the key', async () => {
    mockFetch(() => respond({ detail: 'no' }, 401))
    signedIn()
    renderApp('/tags')
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
  })
})

describe('adding and changing a tag', () => {
  it('checks the tag with the broker, then saves it, and shows it', async () => {
    const { sent, tags } = fakeTags([tag('AAA')])
    await open()
    await screen.findByRole('row', { name: /AAA/ })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Add tag' }))
    const form = within(screen.getByRole('form', { name: 'Add a tag' }))
    await user.type(form.getByLabelText('Id tag'), 'NEW1')
    await user.selectOptions(form.getByLabelText('Status'), 'Blocked')
    await user.selectOptions(form.getByLabelText('Type'), 'NFC')
    await user.type(form.getByLabelText('Parent id tag'), 'ROOT')
    await user.type(form.getByLabelText('Description'), 'Bob')
    await user.click(form.getByRole('button', { name: 'Add tag' }))
    expect(await screen.findByText('Tag NEW1 added successfully')).toBeInTheDocument()
    expect(sent('POST', '/tags/validate')).toHaveLength(1)
    expect(sent('POST', '/tags/validate')[0]?.url).not.toContain('for_update')
    expect(sent('POST', '/tags')[0]?.body).toEqual({ id_tag: 'NEW1', status: 'Blocked', tag_type: 'NFC', parent_id_tag: 'ROOT', description: 'Bob' })
    expect(tags.has('NEW1')).toBe(true)
    expect(screen.queryByRole('form', { name: 'Add a tag' })).not.toBeInTheDocument()
    await screen.findByRole('row', { name: /NEW1/ })
  })

  it('shows what the broker finds wrong and saves nothing', async () => {
    const { sent } = fakeTags([tag('AAA')])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Add tag' }))
    await user.type(screen.getByLabelText('Id tag'), 'AAA')
    await user.click(within(screen.getByRole('form')).getByRole('button', { name: 'Add tag' }))
    expect(await screen.findByText('Tag AAA already exists')).toBeInTheDocument()
    expect(sent('POST', '/tags')).toHaveLength(0)
    expect(screen.getByRole('form', { name: 'Add a tag' })).toBeInTheDocument()
  })

  it('needs an id tag before it asks anything', async () => {
    const { calls } = fakeTags([])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Add tag' }))
    const before = calls.length
    await user.click(within(screen.getByRole('form')).getByRole('button', { name: 'Add tag' }))
    expect(await screen.findByText('The id tag is required.')).toBeInTheDocument()
    expect(calls.length).toBe(before)
  })

  it('shows a warning but still saves', async () => {
    const { tags } = fakeTags([])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Add tag' }))
    await user.type(screen.getByLabelText('Id tag'), 'OLD1')
    await user.type(screen.getByLabelText('Expires'), '2020-01-01T00:00:00Z')
    await user.click(within(screen.getByRole('form')).getByRole('button', { name: 'Add tag' }))
    await screen.findByText('Tag OLD1 added successfully')
    expect(tags.get('OLD1')?.expiry_date).toBe('2020-01-01T00:00:00Z')
  })

  it('reports a failed save and keeps the form', async () => {
    const { fetch } = fakeTags([])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Add tag' }))
    await user.type(screen.getByLabelText('Id tag'), 'NEW1')
    const real = fetch.getMockImplementation()!
    fetch.mockImplementation((url, init) => (init?.method === 'POST' && String(url).endsWith('/tags') ? Promise.resolve(respond({ detail: 'Failed to add tag' }, 400)) : real(url, init)))
    await user.click(within(screen.getByRole('form')).getByRole('button', { name: 'Add tag' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to add tag')
    expect(screen.getByRole('form', { name: 'Add a tag' })).toBeInTheDocument()
  })

  it('changes a tag: the id cannot be edited, the broker is asked for an update, and unseen metadata is kept', async () => {
    const { sent, tags } = fakeTags([tag('AAA', { description: 'old', metadata: { owner: 'Ann' } })])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Edit AAA' }))
    const form = within(screen.getByRole('form', { name: 'Edit tag AAA' }))
    expect(form.getByLabelText('Id tag')).toHaveAttribute('readonly')
    await user.selectOptions(form.getByLabelText('Status'), 'Blocked')
    await user.clear(form.getByLabelText('Description'))
    await user.click(form.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Tag AAA updated successfully')).toBeInTheDocument()
    expect(sent('POST', '/tags/validate')[0]?.url).toContain('for_update=true')
    expect(sent('PUT', '/tags/AAA')[0]?.body).toEqual({ id_tag: 'AAA', status: 'Blocked', tag_type: 'RFID', metadata: { owner: 'Ann' } })
    expect(tags.get('AAA')?.status).toBe('Blocked')
  })

  it('can be cancelled', async () => {
    const { calls } = fakeTags([tag('AAA')])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Add tag' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    expect(calls.filter((c) => c.method !== 'GET')).toEqual([])
  })
})

describe('deleting and bulk changes', () => {
  it('asks before deleting one tag, and does nothing if the answer is no', async () => {
    const { sent, tags } = fakeTags([tag('AAA')])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete AAA' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Confirm delete' })
    expect(dialog).toHaveTextContent('Delete AAA?')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(sent('DELETE', '/tags/AAA')).toHaveLength(0)
    expect(tags.has('AAA')).toBe(true)

    await user.click(screen.getByRole('button', { name: 'Delete AAA' }))
    await user.click(screen.getByRole('button', { name: 'Yes, delete' }))
    expect(await screen.findByText('Tag AAA deleted successfully')).toBeInTheDocument()
    expect(tags.has('AAA')).toBe(false)
    await waitFor(() => expect(screen.queryByRole('row', { name: /AAA/ })).not.toBeInTheDocument())
  })

  it('encodes the id tag in the address', async () => {
    const { sent } = fakeTags([tag('A/B C')])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete A/B C' }))
    await user.click(screen.getByRole('button', { name: 'Yes, delete' }))
    await screen.findByText(/deleted successfully/)
    expect(sent('DELETE', '/tags/A%2FB%20C')).toHaveLength(1)
  })

  it('selects tags and sets them all to Blocked or Accepted in one go', async () => {
    const { tags, sent } = fakeTags([tag('A'), tag('B'), tag('C')])
    await open()
    const user = userEvent.setup()
    await screen.findByRole('rowheader', { name: 'A' })
    await user.click(screen.getByLabelText('Select A'))
    await user.click(screen.getByLabelText('Select B'))
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    await user.click(within(screen.getByRole('group', { name: 'Selected tags' })).getByRole('button', { name: 'Block' }))
    expect(await screen.findByText('Set 2 of 2 to Blocked')).toBeInTheDocument()
    expect(sent('POST', '/tags/bulk')[0]?.body?.operation).toBe('update')
    expect([...tags.values()].map((t) => t.status)).toEqual(['Blocked', 'Blocked', 'Accepted'])
    await user.click(within(screen.getByRole('group', { name: 'Selected tags' })).getByRole('button', { name: 'Accept' }))
    expect(await screen.findByText('Set 2 of 2 to Accepted')).toBeInTheDocument()
    expect([...tags.values()].map((t) => t.status)).toEqual(['Accepted', 'Accepted', 'Accepted'])
  })

  it('selects every tag on the page, and clears the selection', async () => {
    fakeTags([tag('A'), tag('B')])
    await open()
    const user = userEvent.setup()
    await screen.findByRole('rowheader', { name: 'A' })
    await user.click(screen.getByLabelText('Select all tags on this page'))
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    await user.click(screen.getByLabelText('Select all tags on this page'))
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument()
  })

  it('deletes several at once and reports the ones that could not be deleted', async () => {
    const { tags, state } = fakeTags([tag('A'), tag('B'), tag('C')])
    state.failBulkFor.add('B')
    await open()
    const user = userEvent.setup()
    await screen.findByRole('rowheader', { name: 'A' })
    await user.click(screen.getByLabelText('Select all tags on this page'))
    await user.click(screen.getByRole('button', { name: 'Delete…' }))
    expect(screen.getByRole('alertdialog')).toHaveTextContent('Delete 3 tags?')
    await user.click(screen.getByRole('button', { name: 'Yes, delete' }))
    expect(await screen.findByText(/Deleted 2 of 3; 1 failed \(B: locked\)/)).toBeInTheDocument()
    expect([...tags.keys()]).toEqual(['B'])
  })

  it('forgets the selection when the filters change', async () => {
    fakeTags([tag('AAA'), tag('BBB')])
    await open()
    const user = userEvent.setup()
    await screen.findByRole('row', { name: /AAA/ })
    await user.click(screen.getByLabelText('Select AAA'))
    expect(screen.getByText('1 selected')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Search tags'), 'A')
    await waitFor(() => expect(screen.queryByText('1 selected')).not.toBeInTheDocument())
  })

  it('withdraws a delete that was waiting for an answer when the list changes under it', async () => {
    const { sent } = fakeTags([tag('AAA'), tag('BBB')])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete AAA' }))
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Search tags'), 'B')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(sent('DELETE', '/tags/AAA')).toHaveLength(0)
  })

  it('reports an action that fails', async () => {
    const { fetch } = fakeTags([tag('AAA')])
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete AAA' }))
    const real = fetch.getMockImplementation()!
    fetch.mockImplementation((url, init) => (init?.method === 'DELETE' ? Promise.resolve(respond({ detail: 'Failed to delete tag' }, 400)) : real(url, init)))
    await user.click(screen.getByRole('button', { name: 'Yes, delete' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to delete tag')
  })
})

describe('importing', () => {
  const records = [tag('NEW1'), tag('AAA'), tag('X'.repeat(21))]

  async function openImport() {
    const fake = fakeTags([tag('AAA')])
    await open()
    const user = userEvent.setup({ applyAccept: false })
    await user.click(await screen.findByRole('button', { name: 'Import…' }))
    return { ...fake, user, panel: within(screen.getByRole('region', { name: 'Import tags' })) }
  }

  async function paste(user: ReturnType<typeof userEvent.setup>, text: string) {
    const box = screen.getByLabelText('Tags')
    await user.clear(box)
    await user.click(box)
    await user.paste(text)
  }

  it('checks first, says what would happen without changing anything, then imports', async () => {
    const { user, panel, tags, sent } = await openImport()
    expect(panel.getByRole('button', { name: 'Import' })).toBeDisabled()
    await paste(user, JSON.stringify(records))
    await user.click(panel.getByRole('button', { name: 'Check' }))
    const result = await screen.findByRole('status', { name: 'Import result' })
    expect(result).toHaveTextContent('Checked, nothing has been changed yet.')
    expect(result).toHaveTextContent('Of 3 records: 1 would be added, 0 would be updated, 1 would be left alone (already there), 1 rejected.')
    expect(within(result).getByText('id_tag is longer than 20 characters')).toBeInTheDocument()
    expect(tags.has('NEW1')).toBe(false)
    expect(sent('POST', '/tags/import')[0]?.body?.validate_only).toBe(true)

    await user.click(panel.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(tags.has('NEW1')).toBe(true))
    expect(sent('POST', '/tags/import')[1]?.body?.validate_only).toBe(false)
    expect(screen.queryByRole('region', { name: 'Import tags' })).not.toBeInTheDocument()
    expect(await screen.findByRole('status', { name: 'Import result' })).toHaveTextContent('Imported. Of 3 records: 1 were added')
  })

  it('does not allow importing text that was changed after the check', async () => {
    const { user, panel } = await openImport()
    await paste(user, JSON.stringify([tag('NEW1')]))
    await user.click(panel.getByRole('button', { name: 'Check' }))
    await screen.findByRole('status', { name: 'Import result' })
    expect(panel.getByRole('button', { name: 'Import' })).toBeEnabled()
    await user.type(screen.getByLabelText('Tags'), ' ')
    expect(panel.getByRole('button', { name: 'Import' })).toBeDisabled()
    expect(screen.getByText(/changed since the check/)).toBeInTheDocument()
  })

  it('does not allow importing when the check found nothing to add or update', async () => {
    const { user, panel } = await openImport()
    await paste(user, JSON.stringify([tag('AAA')]))
    await user.click(panel.getByRole('button', { name: 'Check' }))
    await screen.findByRole('status', { name: 'Import result' })
    expect(panel.getByRole('button', { name: 'Import' })).toBeDisabled()
  })

  it('can replace existing tags when asked', async () => {
    const { user, panel, sent } = await openImport()
    await paste(user, JSON.stringify([tag('AAA', { status: 'Blocked' })]))
    await user.click(panel.getByLabelText(/Replace tags that already exist/))
    await user.click(panel.getByRole('button', { name: 'Check' }))
    expect(await screen.findByRole('status', { name: 'Import result' })).toHaveTextContent('1 would be updated')
    expect(sent('POST', '/tags/import')[0]?.body?.overwrite_existing).toBe(true)
  })

  it('needs text before it can check', async () => {
    const { panel } = await openImport()
    expect(panel.getByRole('button', { name: 'Check' })).toBeDisabled()
  })

  it('reads a file and picks CSV from its name', async () => {
    const { user, panel } = await openImport()
    const file = new File(['id_tag,status\nNEW1,Accepted\n'], 'tags.csv', { type: 'text/csv' })
    await user.upload(panel.getByLabelText('File'), file)
    await waitFor(() => expect(screen.getByLabelText('Tags')).toHaveValue('id_tag,status\nNEW1,Accepted\n'))
    expect(screen.getByLabelText('Format')).toHaveValue('csv')
    const json = new File(['[]'], 'tags.json', { type: 'application/json' })
    await user.upload(panel.getByLabelText('File'), json)
    await waitFor(() => expect(screen.getByLabelText('Format')).toHaveValue('json'))
  })

  it('says when the text cannot be read at all', async () => {
    const { user, panel, state } = await openImport()
    state.importError = 'Expecting value: line 1 column 1'
    await paste(user, 'not json')
    await user.click(panel.getByRole('button', { name: 'Check' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The text could not be read: Expecting value: line 1 column 1')
  })

  it('can be closed', async () => {
    const { user, panel } = await openImport()
    await user.click(panel.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'Import tags' })).not.toBeInTheDocument()
  })
})

describe('exporting and syncing', () => {
  it('saves the tags as a file, in the format asked for', async () => {
    const { sent } = fakeTags([tag('AAA')])
    const blobs: Blob[] = []
    vi.mocked(URL.createObjectURL).mockImplementation((blob) => {
      blobs.push(blob as Blob)
      return 'blob:fake'
    })
    const clicked: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download)
    })
    await open()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Export CSV' }))
    expect(await screen.findByText('Exported CSV')).toBeInTheDocument()
    expect(clicked).toEqual(['Home-tags.csv'])
    expect(await blobs[0]?.text()).toBe('id_tag,status\nAAA,Accepted\n')
    expect(sent('POST', '/tags/export')[0]?.body).toEqual({ format: 'csv', include_metadata: true })

    await user.click(screen.getByRole('button', { name: 'Export JSON' }))
    await waitFor(() => expect(clicked).toEqual(['Home-tags.csv', 'Home-tags.json']))
    expect(JSON.parse((await blobs[1]?.text()) ?? '[]')[0].id_tag).toBe('AAA')
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2)
  })

  it('syncs with MongoDB and says what that did', async () => {
    const { calls } = fakeTags([tag('AAA')])
    await open()
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Sync with MongoDB' }))
    const result = await screen.findByRole('status', { name: 'Sync result' })
    expect(result).toHaveTextContent('Home: 3 loaded from MongoDB, 0 pushed to MongoDB, 1 dropped')
    expect(calls.find((c) => c.url.startsWith('/api/tags/sync'))?.url).toBe('/api/tags/sync?org_name=Home')
  })

  it('explains that MongoDB is not connected', async () => {
    const { state } = fakeTags([tag('AAA')])
    state.syncFails = true
    await open()
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Sync with MongoDB' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('MongoDB is not connected')
  })
})
