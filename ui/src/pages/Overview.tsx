import { useEffect } from 'react'

import { ApiError, apiGet, type SystemInfo } from '../api/client'
import { useAuth } from '../auth'
import { formatClock, formatDateTime, formatUptime } from '../format'
import { usePolling } from '../usePolling'

const REFRESH_MS = 10_000

function mongoLabel(mongo: SystemInfo['mongodb']): { text: string; tone: 'ok' | 'warn' | 'off' } {
  if (!mongo.configured) return { text: 'Not configured', tone: 'off' }
  if (mongo.connected && mongo.reachable) return { text: `Connected (${mongo.database ?? 'database'})`, tone: 'ok' }
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
  return found
}

export function Overview() {
  const { key, keyRejected } = useAuth()
  const { data, error, updatedAt } = usePolling(
    (signal) => apiGet<SystemInfo>('/api/system/info', key ?? '', signal),
    REFRESH_MS,
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
        </>
      )}
    </section>
  )
}
