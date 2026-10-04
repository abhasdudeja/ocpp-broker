import { useEffect } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, apiGet, withQuery, type ChargerList, type ChargerSummary, type OfflineChargerList, type OrgSummary } from '../api/client'
import { useAuth } from '../auth'
import { Chip, ConnectorStatus } from '../components/Chip'
import { useRefreshOnEvents } from '../events'
import { formatDateTime, formatRelative } from '../format'
import { ShowMore } from '../components/ShowMore'
import { useNow } from '../useNow'
import { useShowMore } from '../useShowMore'
import { usePolling } from '../usePolling'

const REFRESH_MS = 5_000

export function chargerPath(org: string, chargerId: string): string {
  return `/chargers/${encodeURIComponent(org)}/${encodeURIComponent(chargerId)}`
}

function LeaderCell({ charger }: { charger: ChargerSummary }) {
  const { leader } = charger
  return (
    <>
      <span className={`dot ${leader.connected ? 'dot-ok' : 'dot-bad'}`} aria-hidden="true" />
      <span className="visually-hidden">{leader.connected ? 'Connected: ' : 'Not connected: '}</span>
      {leader.local ? 'this broker' : leader.key}
      {charger.buffered_frames > 0 && (
        <div>
          <Chip tone="warn" title="Charger messages held until the leader is reachable">
            {charger.buffered_frames} waiting
          </Chip>
        </div>
      )}
    </>
  )
}

function Row({ charger, now }: { charger: ChargerSummary; now: number }) {
  // Integer-like keys are always iterated in ascending order, so connector 0 (the charger) comes first
  const connectors = Object.entries(charger.connector_statuses)
  const identity = [charger.vendor, charger.model].filter(Boolean).join(' ')
  return (
    <tr>
      <td>
        <Link to={chargerPath(charger.org, charger.charger_id)}>{charger.charger_id}</Link>
        {identity && <div className="muted small">{identity}</div>}
      </td>
      <td>{charger.org}</td>
      <td>
        {charger.mode} <span className="muted small">OCPP {charger.ocpp_version}</span>
      </td>
      <td>
        <LeaderCell charger={charger} />
      </td>
      <td>
        {charger.followers_total === 0 ? (
          <span className="muted">none</span>
        ) : (
          <Chip tone={charger.followers_connected === charger.followers_total ? 'ok' : 'warn'}>
            {charger.followers_connected}/{charger.followers_total} connected
          </Chip>
        )}
      </td>
      <td>
        {connectors.length === 0 ? (
          <span className="muted">not reported</span>
        ) : (
          <ul className="inline-list">
            {connectors.map(([id, status]) => (
              <li key={id}>
                <span className="muted small">{id === '0' ? 'charger' : `#${id}`}</span> <ConnectorStatus status={status} />
              </li>
            ))}
          </ul>
        )}
      </td>
      <td>
        {charger.open_transactions}
        {charger.degraded_transactions > 0 && (
          <div>
            <Chip tone="warn" title="Some backend is skipped for these because the broker never learned its transaction id">
              {charger.degraded_transactions} with a backend skipped
            </Chip>
          </div>
        )}
      </td>
      <td title={charger.last_seen ?? undefined}>{formatRelative(charger.last_seen, now)}</td>
    </tr>
  )
}

export function Chargers() {
  const { key, keyRejected } = useAuth()
  const [params, setParams] = useSearchParams()
  const org = params.get('org') ?? ''
  const q = params.get('q') ?? ''
  const now = useNow()

  const orgs = usePolling((signal) => apiGet<OrgSummary[]>('/api/orgs', key ?? '', signal), 30_000)
  const list = usePolling(
    (signal) => apiGet<ChargerList>(withQuery('/api/chargers', { org, q }), key ?? '', signal),
    REFRESH_MS,
    `${org}\n${q}`,
  )

  // Anything that changes a row (a charger arriving, a status, a link) refreshes the list at once
  useRefreshOnEvents(list.reload, (e) => e.type !== 'command.result' && (org === '' || e.org === org))

  const offline = usePolling(
    (signal) => apiGet<OfflineChargerList>(withQuery('/api/chargers/offline', { org, q }), key ?? '', signal),
    30_000,
    `${org}\n${q}`,
  )
  // A charger arriving or leaving moves it between the two lists
  useRefreshOnEvents(offline.reload, (e) => e.type === 'charger.connected' || e.type === 'charger.disconnected')

  const error = list.error
  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) keyRejected()
  }, [error, keyRejected])

  const setFilter = (name: 'org' | 'q', value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(name, value)
    else next.delete(name)
    setParams(next, { replace: true })
  }

  const filtered = Boolean(org || q)
  const data = list.data
  const rows = useShowMore(data?.chargers ?? [], `${org}|${q}`)
  return (
    <section>
      <header className="page-head">
        <h1>Chargers</h1>
        <p className="muted small">Chargers connected to this broker instance, refreshed every {REFRESH_MS / 1000} s</p>
      </header>

      <div className="filters">
        <label className="visually-hidden" htmlFor="filter-q">
          Search chargers
        </label>
        <input
          id="filter-q"
          type="search"
          placeholder="Search by charger id"
          value={q}
          onChange={(event) => setFilter('q', event.target.value)}
        />
        <label className="visually-hidden" htmlFor="filter-org">
          Organization
        </label>
        <select id="filter-org" value={org} onChange={(event) => setFilter('org', event.target.value)}>
          <option value="">All organizations</option>
          {(orgs.data ?? []).map((o) => (
            <option key={o.name} value={o.name}>
              {o.name}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <p role="alert" className="banner error">
          {data ? 'Could not refresh: ' : 'Could not load: '}
          {error.message}
          {data && ' The list below is from the last successful update.'}
        </p>
      )}

      {!data && !error && <p className="muted">Loading…</p>}

      {data && data.total === 0 && (
        <p className="empty">
          {filtered
            ? 'No connected charger matches.'
            : 'No chargers are connected to this broker instance right now. (Sessions are per process: with several instances, each shows only its own chargers.)'}
        </p>
      )}

      {data && data.total > 0 && (
        <>
          <p className="muted small">
            {data.total} charger{data.total === 1 ? '' : 's'}
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Charger</th>
                  <th scope="col">Organization</th>
                  <th scope="col">Mode</th>
                  <th scope="col">Leader</th>
                  <th scope="col">Followers</th>
                  <th scope="col">Connectors</th>
                  <th scope="col">Open transactions</th>
                  <th scope="col">Last message</th>
                </tr>
              </thead>
              <tbody>
                {rows.visible.map((charger) => (
                  <Row key={`${charger.org}/${charger.charger_id}`} charger={charger} now={now} />
                ))}
              </tbody>
            </table>
          </div>
          <ShowMore hidden={rows.hidden} onClick={rows.showMore} noun="more chargers" />
        </>
      )}

      <Offline result={offline.data} now={now} resetKey={`${org}|${q}`} />
    </section>
  )
}

/** Chargers the broker has seen before (kept in MongoDB) that are not connected now. */
function Offline({ result, now, resetKey }: { result: OfflineChargerList | null; now: number; resetKey: string }) {
  const rows = useShowMore(result?.chargers ?? [], resetKey)
  if (!result || typeof result.available !== 'boolean') return null
  return (
    <section aria-labelledby="offline-heading">
      <h2 id="offline-heading">Not connected now</h2>
      {!result.available ? (
        <p className="muted small">Chargers that have gone are not listed: {result.reason ?? 'they are not remembered'}.</p>
      ) : result.chargers.length === 0 ? (
        <p className="empty">No known charger is missing.</p>
      ) : (
        <>
          <p className="muted small">
            Seen by this broker before and not connected to this instance now
            {result.total > result.chargers.length && `, the ${result.chargers.length} most recent of ${result.total}`}. A charger connected to another instance also appears here.
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Charger</th>
                  <th scope="col">Organization</th>
                  <th scope="col">Mode</th>
                  <th scope="col">Last seen</th>
                  <th scope="col">Last booted</th>
                </tr>
              </thead>
              <tbody>
                {rows.visible.map((c) => (
                  <tr key={`${c.org}/${c.charger_id}`}>
                    <th scope="row">
                      {c.charger_id}
                      {(c.vendor || c.model) && <div className="muted small">{[c.vendor, c.model].filter(Boolean).join(' ')}</div>}
                    </th>
                    <td>{c.org}</td>
                    <td>{c.mode ?? <span className="muted">—</span>}</td>
                    <td title={c.last_seen_at ? formatDateTime(c.last_seen_at) : undefined}>{formatRelative(c.last_seen_at, now)}</td>
                    <td title={c.last_boot_at ? formatDateTime(c.last_boot_at) : undefined}>{c.last_boot_at ? formatRelative(c.last_boot_at, now) : <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ShowMore hidden={rows.hidden} onClick={rows.showMore} noun="more chargers" />
        </>
      )}
    </section>
  )
}
