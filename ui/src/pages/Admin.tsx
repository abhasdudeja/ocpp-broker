import { useEffect } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'

import { ApiError, apiGet, type AdminApplied, type AdminConfig, type AdminOrg, type AuditEntry, type AuditLogPage } from '../api/client'
import { describeMode } from '../admin'
import { useAuth } from '../auth'
import { Chip } from '../components/Chip'
import { formatDateTime } from '../format'
import { usePolling } from '../usePolling'

const REFRESH_MS = 30_000

export const orgPath = (name: string): string => `/admin/orgs/${encodeURIComponent(name)}`

export function AdminDisabled() {
  return (
    <section>
      <header className="page-head">
        <h1>Admin</h1>
      </header>
      <div className="banner warn" role="status">
        <p>
          The admin API is switched off, so organizations cannot be changed from here. Switch it on in the broker&apos;s configuration and restart it:
        </p>
        <pre>{'admin:\n  enabled: true'}</pre>
        <p>Do this only where the API key is as private as the configuration file: it lets whoever holds a key rewrite the file and decide which chargers may connect.</p>
      </div>
    </section>
  )
}

function Organization({ org }: { org: AdminOrg }) {
  const plaintext = org.credentials.filter((c) => c.storage === 'plaintext').length
  return (
    <tr>
      <th scope="row">
        <Link to={orgPath(org.name)}>{org.name}</Link>
      </th>
      <td>{describeMode(org)}</td>
      <td>
        {org.backends.length === 0 ? (
          <span className="muted">none</span>
        ) : (
          <ul className="inline-list">
            {org.backends.map((b, i) => (
              <li key={`${b.id ?? b.url ?? 'local'}-${i}`}>
                {b.local ? 'this broker' : (b.id ?? b.url)} {b.leader && <Chip tone="info">leader</Chip>}
              </li>
            ))}
          </ul>
        )}
      </td>
      <td>
        {org.charger_auth_required ? <Chip tone="ok">chargers must sign in</Chip> : <Chip tone="warn">open to any charger</Chip>}
        <div className="muted small">
          {org.credentials.length} credential{org.credentials.length === 1 ? '' : 's'}
          {plaintext > 0 && ` · ${plaintext} stored as plaintext`}
        </div>
      </td>
      <td>{org.tags}</td>
    </tr>
  )
}

function Organizations({ config }: { config: AdminConfig }) {
  return (
    <>
      {!config.writable && (
        <p role="alert" className="banner warn">
          {config.writable_reason ?? 'The configuration cannot be changed.'}
        </p>
      )}
      {config.organizations.length === 0 ? (
        <p className="empty">No organization is configured. Chargers cannot connect until one is added.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <caption className="visually-hidden">Organizations</caption>
            <thead>
              <tr>
                <th scope="col">Organization</th>
                <th scope="col">How it works</th>
                <th scope="col">Backends</th>
                <th scope="col">Chargers</th>
                <th scope="col">Tags in the file</th>
              </tr>
            </thead>
            <tbody>
              {config.organizations.map((org) => (
                <Organization key={org.name} org={org} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {config.store === 'mongodb' ? (
        <p className="small muted">
          The organizations are kept in MongoDB (<code>{config.path}</code>), shared by every broker instance that uses it. A change reaches the other instances within a few seconds; the earlier versions are kept (the latest {config.keeps_backups}).
        </p>
      ) : (
        <p className="small muted">
          Saving rewrites <code>{config.path}</code>: its comments are not kept, and a copy of the file as it was is kept next to it (the latest {config.keeps_backups}).
        </p>
      )}
    </>
  )
}

function outcomeTone(outcome: AuditEntry['outcome']) {
  return outcome === 'applied' ? 'ok' : outcome === 'refused' ? 'warn' : 'bad'
}

function Audit() {
  const { key } = useAuth()
  const { data, error } = usePolling((signal) => apiGet<AuditLogPage>('/api/admin/audit?limit=100', key ?? '', signal), REFRESH_MS)
  if (error && !data) {
    return (
      <p role="alert" className="banner error">
        Could not load: {error.message}
      </p>
    )
  }
  if (!data) return <p className="muted">Loading…</p>
  if (data.entries.length === 0) return <p className="empty">Nothing has been changed through the admin API yet.</p>
  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <caption className="visually-hidden">Changes made through the admin API, newest first</caption>
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Outcome</th>
              <th scope="col">Who</th>
              <th scope="col">What</th>
            </tr>
          </thead>
          <tbody>
            {data.entries.map((entry, i) => (
              <tr key={`${entry.time}-${i}`}>
                <td>{formatDateTime(entry.time)}</td>
                <td>
                  <Chip tone={outcomeTone(entry.outcome)}>{entry.outcome}</Chip>
                </td>
                <td>
                  {entry.key_label ?? 'unknown key'}
                  <div className="muted small">{entry.source ?? 'unknown address'}</div>
                </td>
                <td>
                  {entry.organizations.length > 0 && <strong>{entry.organizations.join(', ')}</strong>}
                  {entry.summary.length > 0 && (
                    <ul className="plain">
                      {entry.summary.map((line, n) => (
                        <li key={n}>{line}</li>
                      ))}
                    </ul>
                  )}
                  {entry.detail && <div className="muted small">{entry.detail}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.persisted_to && <p className="small muted">Also kept in <code>{data.persisted_to}</code>.</p>}
    </>
  )
}

export function Admin() {
  const { key, keyRejected } = useAuth()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const tab = params.get('tab') === 'audit' ? 'audit' : 'organizations'
  const applied = (location.state as { applied?: AdminApplied } | null)?.applied

  const config = usePolling((signal) => apiGet<AdminConfig>('/api/admin/config', key ?? '', signal), REFRESH_MS)
  const error = config.error
  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) keyRejected()
  }, [error, keyRejected])

  if (error instanceof ApiError && error.status === 403 && !config.data) return <AdminDisabled />

  return (
    <section>
      <header className="page-head">
        <h1>Admin</h1>
        <p className="muted small">Organizations, their backends and the credentials their chargers use. Anyone with an API key can change all of it.</p>
      </header>

      {applied && (
        <p role="status" className="banner ok">
          Changes applied.{applied.backup && ` The file as it was is kept as ${applied.backup}.`}
          {applied.dropped_connections > 0 && ` ${applied.dropped_connections} charger${applied.dropped_connections === 1 ? ' was' : 's were'} disconnected and will reconnect.`}
          {applied.warnings.map((w) => (
            <span key={w} className="block small">
              {w}
            </span>
          ))}
        </p>
      )}

      <nav aria-label="Admin" className="tabs">
        <button type="button" className="tab" aria-current={tab === 'organizations' ? 'page' : undefined} onClick={() => setParams({}, { replace: true })}>
          Organizations
        </button>
        <button type="button" className="tab" aria-current={tab === 'audit' ? 'page' : undefined} onClick={() => setParams({ tab: 'audit' }, { replace: true })}>
          Audit log
        </button>
        {tab === 'organizations' && config.data?.writable && (
          <Link to="/admin/new" className="button tab-action">
            Add organization
          </Link>
        )}
      </nav>

      {tab === 'audit' && <Audit />}
      {tab === 'organizations' && (
        <>
          {error && (
            <p role="alert" className="banner error">
              {config.data ? 'Could not refresh: ' : 'Could not load: '}
              {error.message}
            </p>
          )}
          {!config.data && !error && <p className="muted">Loading…</p>}
          {config.data && <Organizations config={config.data} />}
        </>
      )}
    </section>
  )
}
