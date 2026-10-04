import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  ApiError,
  apiGet,
  apiRequest,
  withQuery,
  type BulkTagResult,
  type OrgSummary,
  type Tag,
  type TagAck,
  type TagImportResult,
  type TagSearch,
  type TagStatistics,
  type TagStatusValue,
  type TagSyncResult,
} from '../api/client'
import { useAuth } from '../auth'
import { Chip, type Tone } from '../components/Chip'
import { TAG_STATUSES, TAG_TYPES, TagForm } from '../components/TagForm'
import { ImportSummary, TagImport } from '../components/TagImport'
import { formatDateTime } from '../format'
import { usePolling } from '../usePolling'

const PAGE = 25
const NONE: Set<string> = new Set()
const REFRESH_MS = 15_000

function tone(status: string): Tone {
  return status === 'Accepted' ? 'ok' : status === 'Blocked' || status === 'Invalid' ? 'bad' : 'warn'
}

/** Save text as a file in the browser (the key is needed to ask for it, so it cannot be a plain link). */
function download(name: string, type: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

type Panel = { kind: 'add' } | { kind: 'edit'; tag: Tag } | { kind: 'import' } | null

function Stats({ stats }: { stats: TagStatistics }) {
  return (
    <dl className="facts">
      <div>
        <dt>Tags</dt>
        <dd>{stats.total_tags}</dd>
      </div>
      <div>
        <dt>Active</dt>
        <dd>{stats.active_tags}</dd>
      </div>
      <div>
        <dt>Expired</dt>
        <dd>{stats.expired_tags}</dd>
      </div>
      <div>
        <dt>Blocked</dt>
        <dd>{stats.blocked_tags}</dd>
      </div>
    </dl>
  )
}

export function Tags() {
  const { key, keyRejected } = useAuth()
  const [params, setParams] = useSearchParams()
  const orgs = usePolling((signal) => apiGet<OrgSummary[]>('/api/orgs', key ?? '', signal), 60_000)
  // Tags matter where the broker itself answers the charger: broker mode, with or without followers
  const tagOrgs = (Array.isArray(orgs.data) ? orgs.data : []).filter((o) => o.mode === 'broker')
  const org = params.get('org') || tagOrgs[0]?.name || ''
  const q = params.get('q') ?? ''
  const status = params.get('status') ?? ''
  const type = params.get('type') ?? ''
  const offset = Number(params.get('offset') ?? '0') || 0

  const [panel, setPanel] = useState<Panel>(null)
  // A selection or a pending delete belongs to the page it was made on: it lapses when the filters or the page change
  const [selection, setSelection] = useState<{ scope: string; ids: Set<string> }>({ scope: '', ids: new Set() })
  const [pending, setPending] = useState<{ scope: string; ids: string[] } | null>(null)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null)
  const [imported, setImported] = useState<TagImportResult | null>(null)
  const [sync, setSync] = useState<TagSyncResult | null>(null)
  const [busy, setBusy] = useState(false)

  const base = `/api/tags/organizations/${encodeURIComponent(org)}`
  const filterKey = `${org}\n${q}\n${status}\n${type}\n${offset}`
  const list = usePolling(
    (signal) =>
      org
        ? apiGet<TagSearch>(withQuery(`${base}/tags`, { id_tag: q, status, tag_type: type, limit: String(PAGE), offset: offset ? String(offset) : undefined }), key ?? '', signal)
        : Promise.resolve(null),
    REFRESH_MS,
    filterKey,
  )
  const stats = usePolling((signal) => (org ? apiGet<TagStatistics>(`${base}/statistics`, key ?? '', signal) : Promise.resolve(null)), REFRESH_MS, org)

  useEffect(() => {
    for (const error of [list.error, stats.error, orgs.error]) if (error instanceof ApiError && error.status === 401) keyRejected()
  }, [list.error, stats.error, orgs.error, keyRejected])

  const setParam = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [name, value] of Object.entries(changes)) {
      if (value) next.set(name, value)
      else next.delete(name)
    }
    if (!('offset' in changes)) next.delete('offset') // a new filter starts at the first page
    setParams(next, { replace: true })
  }

  const refresh = () => {
    list.reload()
    stats.reload()
  }

  async function act<T>(work: () => Promise<T>, done: (result: T) => string | null): Promise<void> {
    setBusy(true)
    setNotice(null)
    try {
      const result = await work()
      const text = done(result)
      if (text) setNotice({ tone: 'ok', text })
      refresh()
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) keyRejected()
      setNotice({ tone: 'bad', text: error instanceof Error ? error.message : 'That did not work' })
    } finally {
      setBusy(false)
    }
  }

  const selected = selection.scope === filterKey ? selection.ids : NONE
  const setSelected = (ids: Set<string>) => setSelection({ scope: filterKey, ids })
  const confirm = pending && pending.scope === filterKey ? pending : null
  const setConfirm = (value: { ids: string[] } | null) => setPending(value && { scope: filterKey, ids: value.ids })

  const rows = list.data?.tags ?? []
  const total = list.data?.total ?? 0
  const allSelected = rows.length > 0 && rows.every((t) => selected.has(t.id_tag))

  const remove = (ids: string[]) =>
    act(
      async () => {
        setConfirm(null)
        if (ids.length === 1) return (await apiRequest<TagAck>('DELETE', `${base}/tags/${encodeURIComponent(ids[0] ?? '')}`, key ?? '')).body.message
        const tags = rows.filter((t) => ids.includes(t.id_tag))
        const { body } = await apiRequest<BulkTagResult>('POST', `${base}/tags/bulk`, key ?? '', { operation: 'delete', tags })
        return `Deleted ${body.succeeded} of ${body.total}${body.failed ? `; ${body.failed} failed (${body.results.filter((r) => !r.success).map((r) => `${r.id_tag}: ${r.error}`).join('; ')})` : ''}`
      },
      (text) => text,
    )

  const setStatusOf = (ids: string[], next: TagStatusValue) =>
    act(
      async () => {
        const tags = rows.filter((t) => ids.includes(t.id_tag)).map((t) => ({ ...t, status: next }))
        return (await apiRequest<BulkTagResult>('POST', `${base}/tags/bulk`, key ?? '', { operation: 'update', tags })).body
      },
      (body) => `Set ${body.succeeded} of ${body.total} to ${next}${body.failed ? `; ${body.failed} failed` : ''}`,
    )

  const exportAs = (format: 'json' | 'csv') =>
    act(
      async () => (await apiRequest<string>('POST', `${base}/tags/export`, key ?? '', { format, include_metadata: true }, { text: true })).body,
      (text) => {
        download(`${org}-tags.${format}`, format === 'csv' ? 'text/csv' : 'application/json', text)
        return `Exported ${format.toUpperCase()}`
      },
    )

  const syncNow = () =>
    act(
      async () => (await apiRequest<TagSyncResult>('POST', withQuery('/api/tags/sync', { org_name: org }), key ?? '')).body,
      (result) => {
        setSync(result)
        return null
      },
    )

  return (
    <section>
      <header className="page-head">
        <h1>Tags</h1>
        <p className="muted small">The id tags the broker authorizes when it answers a charger itself (broker mode, or as a local leader)</p>
      </header>

      {orgs.data && tagOrgs.length === 0 && <p className="empty">No organization lets the broker answer chargers itself, so there are no tags to manage here.</p>}
      {orgs.error && !orgs.data && (
        <p role="alert" className="banner error">
          Could not load: {orgs.error.message}
        </p>
      )}

      {tagOrgs.length > 0 && (
        <>
          <div className="filters">
            <label className="visually-hidden" htmlFor="tag-org">
              Organization
            </label>
            <select id="tag-org" value={org} onChange={(e) => setParam({ org: e.target.value, q: '', status: '', type: '' })}>
              {tagOrgs.map((o) => (
                <option key={o.name}>{o.name}</option>
              ))}
            </select>
            <label className="visually-hidden" htmlFor="tag-q">
              Search tags
            </label>
            <input id="tag-q" type="search" placeholder="Search by id tag" value={q} onChange={(e) => setParam({ q: e.target.value })} />
            <label className="visually-hidden" htmlFor="tag-status">
              Status
            </label>
            <select id="tag-status" value={status} onChange={(e) => setParam({ status: e.target.value })}>
              <option value="">Any status</option>
              {TAG_STATUSES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
            <label className="visually-hidden" htmlFor="tag-type">
              Type
            </label>
            <select id="tag-type" value={type} onChange={(e) => setParam({ type: e.target.value })}>
              <option value="">Any type</option>
              {TAG_TYPES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>

          {stats.data && <Stats stats={stats.data} />}

          <div className="toolbar">
            <button type="button" onClick={() => setPanel({ kind: 'add' })} disabled={busy}>
              Add tag
            </button>
            <button type="button" className="secondary" onClick={() => setPanel({ kind: 'import' })} disabled={busy}>
              Import…
            </button>
            <button type="button" className="secondary" onClick={() => void exportAs('json')} disabled={busy}>
              Export JSON
            </button>
            <button type="button" className="secondary" onClick={() => void exportAs('csv')} disabled={busy}>
              Export CSV
            </button>
            <button type="button" className="secondary" onClick={() => void syncNow()} disabled={busy} title="Reload this organization's tags from MongoDB">
              Sync with MongoDB
            </button>
          </div>

          {notice && (
            <p role={notice.tone === 'bad' ? 'alert' : 'status'} className={`banner ${notice.tone === 'bad' ? 'error' : 'ok'}`}>
              {notice.text}
            </p>
          )}
          {sync && (
            <div role="status" aria-label="Sync result" className="banner ok">
              {Object.entries(sync.organizations).map(([name, s]) => (
                <div key={name}>
                  {name}: {s.loaded} loaded from MongoDB, {s.seeded} pushed to MongoDB, {s.dropped} dropped
                </div>
              ))}
              {Object.keys(sync.organizations).length === 0 && sync.message}
            </div>
          )}
          {imported && <ImportSummary result={imported} checked={false} />}

          {panel?.kind === 'add' && (
            <TagForm
              org={org}
              initial={null}
              onCancel={() => setPanel(null)}
              onSaved={(text) => {
                setPanel(null)
                setNotice({ tone: 'ok', text })
                refresh()
              }}
            />
          )}
          {panel?.kind === 'edit' && (
            <TagForm
              key={panel.tag.id_tag}
              org={org}
              initial={panel.tag}
              onCancel={() => setPanel(null)}
              onSaved={(text) => {
                setPanel(null)
                setNotice({ tone: 'ok', text })
                refresh()
              }}
            />
          )}
          {panel?.kind === 'import' && (
            <TagImport
              org={org}
              onClose={() => setPanel(null)}
              onImported={(result) => {
                setPanel(null)
                setImported(result)
                refresh()
              }}
            />
          )}

          {list.error && (
            <p role="alert" className="banner error">
              {list.data ? 'Could not refresh: ' : 'Could not load: '}
              {list.error.message}
            </p>
          )}

          {selected.size > 0 && (
            <div className="toolbar" role="group" aria-label="Selected tags">
              <strong>{selected.size} selected</strong>
              <button type="button" className="secondary" onClick={() => void setStatusOf([...selected], 'Accepted')} disabled={busy}>
                Accept
              </button>
              <button type="button" className="secondary" onClick={() => void setStatusOf([...selected], 'Blocked')} disabled={busy}>
                Block
              </button>
              <button type="button" className="secondary" onClick={() => setConfirm({ ids: [...selected] })} disabled={busy}>
                Delete…
              </button>
            </div>
          )}
          {confirm && (
            <div className="confirm" role="alertdialog" aria-label="Confirm delete">
              <p>
                <strong>
                  Delete {confirm.ids.length === 1 ? confirm.ids[0] : `${confirm.ids.length} tags`}?
                </strong>{' '}
                A charger presenting {confirm.ids.length === 1 ? 'it' : 'them'} will be answered Invalid.
              </p>
              <button type="button" onClick={() => void remove(confirm.ids)}>
                Yes, delete
              </button>{' '}
              <button type="button" className="secondary" onClick={() => setConfirm(null)}>
                Cancel
              </button>
            </div>
          )}

          {!list.data && !list.error && <p className="muted">Loading…</p>}
          {list.data && rows.length === 0 && (
            <p className="empty">{q || status || type ? 'No tag matches.' : 'This organization has no tags yet. Add one, or import a list.'}</p>
          )}
          {rows.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">
                      <input
                        type="checkbox"
                        aria-label="Select all tags on this page"
                        checked={allSelected}
                        onChange={(e) => setSelected(e.target.checked ? new Set(rows.map((t) => t.id_tag)) : new Set())}
                      />
                    </th>
                    <th scope="col">Id tag</th>
                    <th scope="col">Status</th>
                    <th scope="col">Type</th>
                    <th scope="col">Expires</th>
                    <th scope="col">Parent</th>
                    <th scope="col">Description</th>
                    <th scope="col">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((tag) => (
                    <tr key={tag.id_tag}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${tag.id_tag}`}
                          checked={selected.has(tag.id_tag)}
                          onChange={(e) => {
                            const next = new Set(selected)
                            if (e.target.checked) next.add(tag.id_tag)
                            else next.delete(tag.id_tag)
                            setSelected(next)
                          }}
                        />
                      </td>
                      <th scope="row">{tag.id_tag}</th>
                      <td>
                        <Chip tone={tone(tag.status)}>{tag.status}</Chip>
                      </td>
                      <td>{tag.tag_type}</td>
                      <td>{tag.expiry_date ? formatDateTime(tag.expiry_date) : <span className="muted">never</span>}</td>
                      <td>{tag.parent_id_tag ?? <span className="muted">—</span>}</td>
                      <td>{tag.description ?? <span className="muted">—</span>}</td>
                      <td>
                        <button type="button" className="secondary" onClick={() => setPanel({ kind: 'edit', tag })} aria-label={`Edit ${tag.id_tag}`} disabled={busy}>
                          Edit
                        </button>{' '}
                        <button type="button" className="secondary" onClick={() => setConfirm({ ids: [tag.id_tag] })} aria-label={`Delete ${tag.id_tag}`} disabled={busy}>
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {total > PAGE && (
            <nav className="pager" aria-label="Pages">
              <button type="button" className="secondary" disabled={offset === 0} onClick={() => setParam({ offset: String(Math.max(0, offset - PAGE)) })}>
                Previous
              </button>
              <span className="muted small">
                {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
              </span>
              <button type="button" className="secondary" disabled={offset + PAGE >= total} onClick={() => setParam({ offset: String(offset + PAGE) })}>
                Next
              </button>
            </nav>
          )}
        </>
      )}
    </section>
  )
}
