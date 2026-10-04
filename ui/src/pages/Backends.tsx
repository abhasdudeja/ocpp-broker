import { useEffect } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, apiGet, type BackendStat, type OrgBackends } from '../api/client'
import { useAuth } from '../auth'
import { Chip } from '../components/Chip'
import { EventFeed } from '../components/EventFeed'
import { useRefreshOnEvents } from '../events'
import { usePolling } from '../usePolling'
import { chargerPath } from './Chargers'

const REFRESH_MS = 5_000
const LISTED_DOWN = 20 // the broker names at most this many

function Row({ org, stat }: { org: string; stat: BackendStat }) {
  return (
    <tr>
      <th scope="row">
        {stat.key}{' '}
        {stat.local && <Chip title="This broker itself: it answers the charger, the other backends only receive copies">this broker</Chip>}
        {stat.url && <div className="muted small node-url">{stat.url}</div>}
      </th>
      <td>{stat.configured_leader ? <Chip tone="info">leader</Chip> : <span className="muted">follower</span>}</td>
      <td>{stat.leading}</td>
      <td>{stat.following}</td>
      <td>
        <span className={`dot ${stat.links_down === 0 ? 'dot-ok' : 'dot-bad'}`} aria-hidden="true" />
        {stat.links_up} up
        {stat.links_down > 0 && <Chip tone="bad">{stat.links_down} down</Chip>}
      </td>
      <td>
        {stat.buffered_frames > 0 ? (
          <Chip tone="warn" title="Charger messages held until this backend is reachable">
            {stat.buffered_frames} waiting
          </Chip>
        ) : (
          <span className="muted">none</span>
        )}
      </td>
      <td>
        {stat.down_chargers.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          <ul className="inline-list">
            {stat.down_chargers.map((id) => (
              <li key={id}>
                <Link to={chargerPath(org, id)}>{id}</Link>
              </li>
            ))}
            {stat.links_down > stat.down_chargers.length && stat.down_chargers.length >= LISTED_DOWN && <li className="muted small">and {stat.links_down - stat.down_chargers.length} more</li>}
          </ul>
        )}
      </td>
    </tr>
  )
}

export function Backends() {
  const { key, keyRejected } = useAuth()
  const { data, error, reload } = usePolling((signal) => apiGet<OrgBackends[]>('/api/backends', key ?? '', signal), REFRESH_MS)
  useRefreshOnEvents(reload, (e) => e.type.startsWith('backend.') || e.type === 'charger.connected' || e.type === 'charger.disconnected')

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) keyRejected()
  }, [error, keyRejected])

  const orgs = Array.isArray(data) ? data : []
  return (
    <section>
      <header className="page-head">
        <h1>Backends</h1>
        <p className="muted small">How each organization&apos;s backends stand across the chargers connected to this instance, refreshed every {REFRESH_MS / 1000} s</p>
      </header>

      {error && (
        <p role="alert" className="banner error">
          {data ? 'Could not refresh: ' : 'Could not load: '}
          {error.message}
        </p>
      )}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && orgs.length === 0 && <p className="empty">No organization has backends: either the broker answers every charger itself, or none is configured.</p>}

      {orgs.map((o) => (
        <section key={o.org} aria-labelledby={`org-${o.org}`}>
          <h2 id={`org-${o.org}`}>
            {o.org}{' '}
            <span className="muted small">
              {o.chargers} charger{o.chargers === 1 ? '' : 's'} connected
              {o.mode === 'broker' && ' · the broker answers, the others receive copies'}
            </span>
          </h2>
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Backends of {o.org}</caption>
              <thead>
                <tr>
                  <th scope="col">Backend</th>
                  <th scope="col">Configured as</th>
                  <th scope="col">Leads</th>
                  <th scope="col">Follows</th>
                  <th scope="col">Links</th>
                  <th scope="col">Messages waiting</th>
                  <th scope="col">Chargers that lost it</th>
                </tr>
              </thead>
              <tbody>
                {o.backends.map((stat) => (
                  <Row key={stat.key} org={o.org} stat={stat} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}

      <EventFeed types={['backend.link', 'backend.failover']} title="Recent backend events" />
    </section>
  )
}
