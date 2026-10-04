import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import {
  ApiError,
  apiGet,
  type CommandRecord,
  type HistoryInfo,
  type HistoryTransaction,
  type MessageRecord,
  type OrgSummary,
  type StatusRecord,
} from '../api/client'
import { useAuth } from '../auth'
import { Chip, ConnectorStatus } from '../components/Chip'
import { formatDateTime } from '../format'
import { formatDuration, formatEnergy, RANGES, sinceFor } from '../history'
import { useNow } from '../useNow'
import { usePages, type Pages } from '../usePages'
import { usePolling } from '../usePolling'
import { chargerPath } from './Chargers'

const TABS = [
  { id: 'transactions', label: 'Transactions' },
  { id: 'statuses', label: 'Status changes' },
  { id: 'commands', label: 'Commands' },
  { id: 'messages', label: 'Messages' },
] as const
type Tab = (typeof TABS)[number]['id']

export function transactionPath(org: string, chargerId: string, transactionId: number): string {
  return `/history/transactions/${encodeURIComponent(org)}/${encodeURIComponent(chargerId)}/${transactionId}`
}

const pretty = (value: unknown): string => JSON.stringify(value, null, 2)

function Json({ summary, value }: { summary: string; value: unknown }) {
  if (value === null || value === undefined) return <span className="muted">—</span>
  return (
    <details>
      <summary>{summary}</summary>
      <pre>{pretty(value)}</pre>
    </details>
  )
}

function Chargerlink({ org, id }: { org: string; id: string }) {
  return (
    <>
      <Link to={chargerPath(org, id)}>{id}</Link>
      <div className="muted small">{org}</div>
    </>
  )
}

function commandTone(status: string) {
  return status === 'success' ? 'ok' : status === 'pending' ? 'neutral' : 'bad'
}

/** The state of a list: why it is empty, an error, a button for the next page. */
function ListState<T>({ pages, empty, children }: { pages: Pages<T>; empty: string; children: ReactNode }) {
  if (pages.error && pages.items.length === 0) {
    return (
      <p role="alert" className="banner error">
        Could not load: {pages.error.message}
      </p>
    )
  }
  if (pages.loading && pages.items.length === 0) return <p className="muted">Loading…</p>
  if (!pages.available) return <p className="empty">{pages.reason ?? 'History is not available.'}</p>
  if (pages.items.length === 0) return <p className="empty">{empty}</p>
  return (
    <>
      {pages.error && (
        <p role="alert" className="banner error">
          Could not load more: {pages.error.message}
        </p>
      )}
      <div className="table-wrap">{children}</div>
      {pages.hasMore && (
        <p>
          <button type="button" onClick={pages.more} disabled={pages.loadingMore}>
            {pages.loadingMore ? 'Loading…' : 'Load older'}
          </button>
        </p>
      )}
    </>
  )
}

interface Filters {
  org: string
  charger: string
  /** The range chosen (an id such as 24h, empty for all time) */
  since: string
  /** The start of that range as a time, counted from when it was chosen, so the list does not shift while it is read */
  sinceIso: string | undefined
  state: string
  tag: string
  status: string
  action: string
  direction: string
}

function Transactions({ filters, now }: { filters: Filters; now: number }) {
  const pages = usePages<HistoryTransaction>('/api/history/transactions', {
    org: filters.org,
    charger_id: filters.charger,
    since: filters.sinceIso,
    state: filters.state,
    id_tag: filters.tag,
  })
  return (
    <ListState pages={pages} empty="No transaction matches. Only transactions the broker itself answered are recorded here (broker mode or a local leader).">
      <table className="table">
        <caption className="visually-hidden">Transactions, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Transaction</th>
            <th scope="col">Charger</th>
            <th scope="col">Connector</th>
            <th scope="col">Tag</th>
            <th scope="col">Started</th>
            <th scope="col">Duration</th>
            <th scope="col">Energy</th>
            <th scope="col">Ended</th>
          </tr>
        </thead>
        <tbody>
          {pages.items.map((t) => (
            <tr key={`${t.org}/${t.charger_id}/${t.transaction_id}`}>
              <td>
                <Link to={transactionPath(t.org, t.charger_id, t.transaction_id)}>#{t.transaction_id}</Link>
              </td>
              <td>
                <Chargerlink org={t.org} id={t.charger_id} />
              </td>
              <td>{t.connector_id ?? '—'}</td>
              <td>{t.id_tag ?? '—'}</td>
              <td>{t.started_at ? formatDateTime(t.started_at) : '—'}</td>
              <td>{formatDuration(t.started_at, t.stopped_at, now)}</td>
              <td>{formatEnergy(t.energy_wh)}</td>
              <td>{t.open ? <Chip tone="info">running</Chip> : (t.stop_reason ?? 'ended')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </ListState>
  )
}

function Statuses({ filters }: { filters: Filters }) {
  const pages = usePages<StatusRecord>('/api/history/statuses', {
    org: filters.org,
    charger_id: filters.charger,
    since: filters.sinceIso,
    status: filters.status,
  })
  return (
    <ListState pages={pages} empty="No status change matches.">
      <table className="table">
        <caption className="visually-hidden">Connector status changes, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Charger</th>
            <th scope="col">Connector</th>
            <th scope="col">Status</th>
            <th scope="col">Error</th>
          </tr>
        </thead>
        <tbody>
          {pages.items.map((s, i) => (
            <tr key={`${s.timestamp}-${s.org}-${s.charger_id}-${s.connector_id}-${i}`}>
              <td>{s.timestamp ? formatDateTime(s.timestamp) : '—'}</td>
              <td>
                <Chargerlink org={s.org} id={s.charger_id} />
              </td>
              <td>{s.connector_id === 0 ? 'charger' : (s.connector_id ?? '—')}</td>
              <td>
                <ConnectorStatus status={s.status} />
              </td>
              <td>
                {s.error_code && s.error_code !== 'NoError' ? <Chip tone="bad">{s.error_code}</Chip> : <span className="muted">—</span>}
                {s.info && <div className="muted small">{s.info}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </ListState>
  )
}

function Commands({ filters }: { filters: Filters }) {
  const pages = usePages<CommandRecord>('/api/history/commands', {
    org: filters.org,
    charger_id: filters.charger,
    since: filters.sinceIso,
    action: filters.action,
    status: filters.status,
  })
  return (
    <ListState pages={pages} empty="No command matches. Commands sent through the API or this console are recorded when they finish.">
      <table className="table">
        <caption className="visually-hidden">Commands, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Sent</th>
            <th scope="col">Charger</th>
            <th scope="col">Command</th>
            <th scope="col">Outcome</th>
            <th scope="col">Took</th>
            <th scope="col">Details</th>
          </tr>
        </thead>
        <tbody>
          {pages.items.map((c) => (
            <tr key={`${c.org}/${c.charger_id}/${c.message_id}`}>
              <td>{c.sent_at ? formatDateTime(c.sent_at) : '—'}</td>
              <td>
                <Chargerlink org={c.org} id={c.charger_id} />
              </td>
              <td>{c.action}</td>
              <td>
                <Chip tone={commandTone(c.status)}>{c.status}</Chip>
                {c.error && <div className="muted small">{c.error}</div>}
              </td>
              <td>{c.duration_ms === null ? '—' : `${c.duration_ms} ms`}</td>
              <td>
                <Json summary="Sent" value={c.payload} />
                <Json summary="Answer" value={c.response} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </ListState>
  )
}

function Messages({ filters, enabled }: { filters: Filters; enabled: boolean | undefined }) {
  const pages = usePages<MessageRecord>('/api/history/messages', {
    org: filters.org,
    charger_id: filters.charger,
    since: filters.sinceIso,
    action: filters.action,
    direction: filters.direction,
  })
  return (
    <>
      {enabled === false && (
        <p className="banner warn">
          The message log is off, so nothing new is recorded here. Set <code>mongodb.history.messages: true</code> to record every OCPP frame (it is one write per message; set a retention too).
        </p>
      )}
      <ListState pages={pages} empty="No message matches.">
        <table className="table">
          <caption className="visually-hidden">OCPP messages, newest first</caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Charger</th>
              <th scope="col">Direction</th>
              <th scope="col">Message</th>
              <th scope="col">Content</th>
            </tr>
          </thead>
          <tbody>
            {pages.items.map((m, i) => (
              <tr key={`${m.timestamp}-${m.message_id}-${m.type}-${i}`}>
                <td>{m.timestamp ? formatDateTime(m.timestamp) : '—'}</td>
                <td>
                  <Chargerlink org={m.org} id={m.charger_id} />
                </td>
                <td>{m.direction === 'in' ? 'from charger' : 'to charger'}</td>
                <td>
                  {m.type === 'call' ? m.action : `${m.action ?? 'reply'} ${m.type === 'error' ? 'error' : 'result'}`}
                  <div className="muted small node-url">{m.message_id}</div>
                </td>
                <td>
                  {m.error && (
                    <div>
                      <Chip tone="bad">{m.error.code ?? 'error'}</Chip> {m.error.description}
                    </div>
                  )}
                  {m.truncated ? <span className="muted">too large to keep{m.size ? ` (${m.size} bytes)` : ''}</span> : <Json summary="Payload" value={m.payload} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ListState>
    </>
  )
}

export function History() {
  const { key, keyRejected } = useAuth()
  const [params, setParams] = useSearchParams()
  const now = useNow()
  const tab: Tab = TABS.find((t) => t.id === params.get('tab'))?.id ?? 'transactions'
  // "Last 24 hours" means 24 hours before this moment: fixed when chosen (or on Refresh), not sliding while rows are read
  const [anchor, setAnchor] = useState(() => ({ at: Date.now(), round: 0 }))
  const range = params.get('since') ?? ''
  const filters: Filters = useMemo(
    () => ({
      org: params.get('org') ?? '',
      charger: params.get('charger') ?? '',
      since: range,
      sinceIso: sinceFor(range, anchor.at),
      state: params.get('state') ?? '',
      tag: params.get('tag') ?? '',
      status: params.get('status') ?? '',
      action: params.get('action') ?? '',
      direction: params.get('direction') ?? '',
    }),
    [params, range, anchor.at],
  )

  const orgs = usePolling((signal) => apiGet<OrgSummary[]>('/api/orgs', key ?? '', signal), 60_000)
  const info = usePolling((signal) => apiGet<HistoryInfo>('/api/history/info', key ?? '', signal), 60_000)
  const error = info.error
  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) keyRejected()
  }, [error, keyRejected])

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(name, value)
    else next.delete(name)
    setParams(next, { replace: true })
    if (name === 'since') setAnchor((a) => ({ at: Date.now(), round: a.round }))
  }
  const refresh = () => {
    setAnchor((a) => ({ at: Date.now(), round: a.round + 1 }))
    info.reload()
  }
  const openTab = (id: Tab) => {
    // Each tab has filters of its own; the ones that are common stay
    const next = new URLSearchParams()
    for (const name of ['org', 'charger', 'since']) {
      const value = params.get(name)
      if (value) next.set(name, value)
    }
    next.set('tab', id)
    setParams(next, { replace: true })
  }

  const field = (id: string, label: string, children: ReactNode) => (
    <>
      <label className="visually-hidden" htmlFor={id}>
        {label}
      </label>
      {children}
    </>
  )

  return (
    <section>
      <header className="page-head">
        <h1>History</h1>
        <p className="muted small">What the broker recorded in MongoDB, newest first. Transactions and meter readings are recorded when the broker itself answers the charger.</p>
      </header>

      {info.data && !info.data.available && <p className="banner warn">{info.data.reason}</p>}
      {error && !info.data && (
        <p role="alert" className="banner error">
          Could not load: {error.message}
        </p>
      )}

      <nav aria-label="History" className="tabs">
        {TABS.map((t) => (
          <button key={t.id} type="button" className="tab" aria-current={t.id === tab ? 'page' : undefined} onClick={() => openTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>

      <div className="filters">
        {field(
          'f-org',
          'Organization',
          <select id="f-org" value={filters.org} onChange={(e) => set('org', e.target.value)}>
            <option value="">All organizations</option>
            {(orgs.data ?? []).map((o) => (
              <option key={o.name} value={o.name}>
                {o.name}
              </option>
            ))}
          </select>,
        )}
        {field('f-charger', 'Charger id', <input id="f-charger" type="search" placeholder="Charger id (exact)" value={filters.charger} onChange={(e) => set('charger', e.target.value)} />)}
        {field(
          'f-since',
          'Time range',
          <select id="f-since" value={filters.since} onChange={(e) => set('since', e.target.value)}>
            <option value="">All time</option>
            {RANGES.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>,
        )}
        {tab === 'transactions' && (
          <>
            {field(
              'f-state',
              'State',
              <select id="f-state" value={filters.state} onChange={(e) => set('state', e.target.value)}>
                <option value="">Running and ended</option>
                <option value="open">Running</option>
                <option value="closed">Ended</option>
              </select>,
            )}
            {field('f-tag', 'Id tag', <input id="f-tag" type="search" placeholder="Id tag (exact)" value={filters.tag} onChange={(e) => set('tag', e.target.value)} />)}
          </>
        )}
        {tab === 'statuses' &&
          field('f-status', 'Status', <input id="f-status" type="search" placeholder="Status, e.g. Faulted" value={filters.status} onChange={(e) => set('status', e.target.value)} />)}
        {tab === 'commands' && (
          <>
            {field('f-action', 'Command', <input id="f-action" type="search" placeholder="Command, e.g. Reset" value={filters.action} onChange={(e) => set('action', e.target.value)} />)}
            {field(
              'f-cstatus',
              'Outcome',
              <select id="f-cstatus" value={filters.status} onChange={(e) => set('status', e.target.value)}>
                <option value="">Any outcome</option>
                <option value="success">success</option>
                <option value="error">error</option>
                <option value="timeout">timeout</option>
                <option value="cancelled">cancelled</option>
              </select>,
            )}
          </>
        )}
        {tab === 'messages' && (
          <>
            {field('f-maction', 'Action', <input id="f-maction" type="search" placeholder="Action, e.g. StatusNotification" value={filters.action} onChange={(e) => set('action', e.target.value)} />)}
            {field(
              'f-direction',
              'Direction',
              <select id="f-direction" value={filters.direction} onChange={(e) => set('direction', e.target.value)}>
                <option value="">Both directions</option>
                <option value="in">From the charger</option>
                <option value="out">To the charger</option>
              </select>,
            )}
          </>
        )}
        <button type="button" onClick={refresh}>
          Refresh
        </button>
      </div>

      {tab === 'transactions' && <Transactions key={anchor.round} filters={filters} now={now} />}
      {tab === 'statuses' && <Statuses key={anchor.round} filters={filters} />}
      {tab === 'commands' && <Commands key={anchor.round} filters={filters} />}
      {tab === 'messages' && <Messages key={anchor.round} filters={filters} enabled={info.data?.messages_enabled} />}
    </section>
  )
}
