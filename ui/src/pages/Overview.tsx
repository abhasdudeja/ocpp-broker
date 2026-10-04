import { useEffect } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, apiGet, type OrgSummary, type SystemInfo } from '../api/client'
import { useAuth } from '../auth'
import { Chip } from '../components/Chip'
import { EventFeed } from '../components/EventFeed'
import { useRefreshOnEvents } from '../events'
import { formatClock, formatDateTime, formatUptime } from '../format'
import { usePolling } from '../usePolling'

const REFRESH_MS = 10_000

function mongoLabel(mongo: SystemInfo['mongodb']): { text: string; tone: 'ok' | 'warn' | 'off' } {
  if (!mongo.configured) return { text: 'Not configured', tone: 'off' }
  if (mongo.connected && mongo.reachable) {
    const waiting = mongo.pending_writes > 0 ? ` · ${mongo.pending_writes} record${mongo.pending_writes === 1 ? '' : 's'} waiting to be written` : ''
    return { text: `Connected (${mongo.database ?? 'database'})${waiting}`, tone: 'ok' }
  }
  if (mongo.reachable) return { text: 'Reachable now, but the broker did not connect at startup', tone: 'warn' }
  return { text: 'Not answering', tone: 'warn' }
}

function warnings(info: SystemInfo): string[] {
  const found: string[] = []
  if (info.api_auth === 'none') {
    found.push('The REST API accepts requests without a key (security.allow_unauthenticated_api is true).')
  }
  if (info.mongodb.configured && !info.mongodb.reachable) {
    found.push('MongoDB is configured but not answering: data is not being stored until it is back.')
  }
  const { writes_degraded: degraded, pending_writes: pending, dropped_writes: dropped } = info.mongodb
  if (degraded) {
    found.push(`MongoDB is not accepting writes: ${pending} record${pending === 1 ? '' : 's'} ${pending === 1 ? 'is' : 'are'} waiting and will be stored when it answers again.`)
  }
  if (dropped > 0) {
    found.push(`${dropped} record${dropped === 1 ? ' was' : 's were'} dropped because too many were waiting for MongoDB; ${dropped === 1 ? 'it is' : 'they are'} not stored.`)
  }
  return found
}

function Organizations({ orgs }: { orgs: OrgSummary[] }) {
  return (
    <section aria-labelledby="orgs-heading">
      <h2 id="orgs-heading">Organizations</h2>
      {orgs.length === 0 ? (
        <p className="empty">No organizations are configured, so every charger connection is refused.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Mode</th>
                <th scope="col">Connected chargers</th>
                <th scope="col">Backends</th>
                <th scope="col">Charger authentication</th>
              </tr>
            </thead>
            <tbody>
              {orgs.map((org) => (
                <tr key={org.name}>
                  <th scope="row">{org.name}</th>
                  <td>
                    {org.mode} <span className="muted small">OCPP {org.ocpp_version}</span>
                  </td>
                  <td>
                    {org.connected_chargers > 0 ? (
                      <Link to={`/chargers?org=${encodeURIComponent(org.name)}`}>{org.connected_chargers}</Link>
                    ) : (
                      0
                    )}
                  </td>
                  <td>
                    {org.backends.length === 0 ? (
                      <span className="muted">{org.mode === 'broker' ? 'this broker' : 'none'}</span>
                    ) : (
                      <ul className="inline-list">
                        {org.backends.map((b) => (
                          <li key={b.key} title={b.url ?? 'This broker itself answers the charger'}>
                            {b.key}{' '}
                            {b.local && <Chip title="This broker itself: it answers the charger, the other backends only receive copies">this broker</Chip>}{' '}
                            {b.leader && <Chip tone="info">leader</Chip>}
                          </li>
                        ))}
                        {org.transaction_id_mapping && (
                          <li>
                            <Chip title="Each backend is spoken to in its own transaction ids">ids translated</Chip>
                          </li>
                        )}
                      </ul>
                    )}
                  </td>
                  <td>{org.charger_auth_required ? 'required' : <span className="muted">not required</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function Overview() {
  const { key, keyRejected } = useAuth()
  const info = usePolling((signal) => apiGet<SystemInfo>('/api/system/info', key ?? '', signal), REFRESH_MS)
  const { data, error, updatedAt } = info
  const orgs = usePolling((signal) => apiGet<OrgSummary[]>('/api/orgs', key ?? '', signal), REFRESH_MS)
  // Connected-charger counts change when a charger arrives or leaves
  const reloadInfo = info.reload
  const reloadOrgs = orgs.reload
  useRefreshOnEvents(
    () => {
      reloadInfo()
      reloadOrgs()
    },
    (e) => e.type === 'charger.connected' || e.type === 'charger.disconnected',
  )

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) keyRejected()
  }, [error, keyRejected])

  return (
    <section>
      <header className="page-head">
        <h1>Overview</h1>
        {updatedAt && (
          <p className="muted small">
            Updated {formatClock(updatedAt)} · refreshes every {REFRESH_MS / 1000} s
          </p>
        )}
      </header>

      {error && (
        <p role="alert" className="banner error">
          {data ? 'Could not refresh: ' : 'Could not load: '}
          {error.message}
          {data && ' The figures below are from the last successful update.'}
        </p>
      )}

      {!data && !error && <p className="muted">Loading…</p>}

      {data && (
        <>
          {warnings(data).map((text) => (
            <p key={text} className="banner warn">
              {text}
            </p>
          ))}

          <dl className="facts">
            <div>
              <dt>Broker version</dt>
              <dd>{data.version}</dd>
            </div>
            <div>
              <dt>Instance</dt>
              <dd>
                <code>{data.instance_id}</code>
              </dd>
            </div>
            <div>
              <dt>Uptime</dt>
              <dd title={`Started ${formatDateTime(data.started_at)}`}>{formatUptime(data.uptime_seconds)}</dd>
            </div>
            <div>
              <dt>Organizations</dt>
              <dd>{data.organizations}</dd>
            </div>
            <div>
              <dt>Connected chargers</dt>
              <dd>
                {data.connected_chargers}
                <span className="muted small"> on this instance</span>
              </dd>
            </div>
            <div>
              <dt>API access</dt>
              <dd>{data.api_auth === 'api_key' ? 'API key required' : 'Open (no key)'}</dd>
            </div>
            <div>
              <dt>MongoDB</dt>
              <dd className={`tone-${mongoLabel(data.mongodb).tone}`}>{mongoLabel(data.mongodb).text}</dd>
            </div>
            <div>
              <dt>Console</dt>
              <dd>{data.ui_built ? 'Built into this installation' : 'Not built'}</dd>
            </div>
          </dl>
          {Array.isArray(orgs.data) && <Organizations orgs={orgs.data} />}
          <EventFeed />
        </>
      )}
    </section>
  )
}
