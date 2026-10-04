import { useEffect, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, apiGet, type ChargerDetail as Detail, type TransactionRow } from '../api/client'
import { useAuth } from '../auth'
import { Chip, ConnectorStatus } from '../components/Chip'
import { Topology } from '../components/Topology'
import { formatDateTime, formatRelative } from '../format'
import { useNow } from '../useNow'
import { usePolling } from '../usePolling'

const REFRESH_MS = 3_000

function Facts({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <dl className="kv">
      {items.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{value ?? <span className="muted">—</span>}</dd>
        </div>
      ))}
    </dl>
  )
}

function Transactions({ detail }: { detail: Detail }) {
  const keys = detail.backends.map((b) => b.key)
  const rows = detail.transactions.filter((row) => row.state !== 'pending' || row.transaction_id !== null || Object.keys(row.backend_ids).length > 0)
  return (
    <section aria-labelledby="tx-heading">
      <h2 id="tx-heading">Transactions</h2>
      {!detail.transaction_id_mapping && (
        <p className="muted small">
          Transaction ids are not translated for this charger (one backend, or the broker answers it itself), so the id the charger holds
          is the backend&apos;s own.
        </p>
      )}
      {rows.length === 0 ? (
        <p className="empty">{detail.transaction_id_mapping ? 'No transactions tracked for this charger yet.' : 'Nothing to show.'}</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Charger holds</th>
                <th scope="col">State</th>
                {keys.map((key) => (
                  <th scope="col" key={key}>
                    {key}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <TransactionLine key={`${row.transaction_id ?? 'pending'}-${index}`} row={row} keys={keys} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function TransactionLine({ row, keys }: { row: TransactionRow; keys: string[] }) {
  return (
    <tr>
      <td>{row.transaction_id ?? <span className="muted">not yet assigned</span>}</td>
      <td>
        <Chip tone={row.state === 'open' ? 'info' : 'neutral'}>{row.state}</Chip>
      </td>
      {keys.map((key) => {
        const own = row.backend_ids[key]
        let cell: ReactNode = <span className="muted">—</span>
        if (own !== undefined) cell = own
        else if (row.degraded.includes(key)) {
          cell = (
            <Chip tone="warn" title="The broker never learned which id this backend gave it, so it is not sent this transaction's messages">
              skipped
            </Chip>
          )
        } else if (row.awaiting.includes(key)) cell = <Chip>waiting for its id</Chip>
        return <td key={key}>{cell}</td>
      })}
    </tr>
  )
}

function IdTable({ title, rows, keys }: { title: string; rows: Detail['reservations']; keys: string[] }) {
  if (rows.length === 0) return null
  return (
    <section>
      <h2>{title}</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Charger holds</th>
              {keys.map((key) => (
                <th scope="col" key={key}>
                  {key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.id}</td>
                {keys.map((key) => (
                  <td key={key}>{row.backend_ids[key] ?? <span className="muted">—</span>}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ChargerView({ org, chargerId }: { org: string; chargerId: string }) {
  const { key, keyRejected } = useAuth()
  const now = useNow()
  const { data, error } = usePolling(
    (signal) => apiGet<Detail>(`/api/chargers/${encodeURIComponent(org)}/${encodeURIComponent(chargerId)}`, key ?? '', signal),
    REFRESH_MS,
  )

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) keyRejected()
  }, [error, keyRejected])

  const gone = error instanceof ApiError && error.status === 404
  const keys = data ? data.backends.map((b) => b.key) : []

  return (
    <section>
      <p className="crumb">
        <Link to="/chargers">← Chargers</Link>
      </p>
      <header className="page-head">
        <h1>{chargerId}</h1>
        <p className="muted small">
          {org}
          {data && ` · ${data.mode} mode · OCPP ${data.ocpp_version}`}
        </p>
      </header>

      {gone && (
        <p role="alert" className="banner warn">
          This charger is not connected to this broker instance{data ? ' any more. What is shown was true when it last was.' : '.'} This
          page checks again every {REFRESH_MS / 1000} s.
        </p>
      )}
      {error && !gone && (
        <p role="alert" className="banner error">
          {data ? 'Could not refresh: ' : 'Could not load: '}
          {error.message}
        </p>
      )}
      {!data && !error && <p className="muted">Loading…</p>}

      {data && (
        <>
          <section aria-labelledby="topology-heading">
            <h2 id="topology-heading">Connections</h2>
            <Topology chargerId={data.charger_id} mode={data.mode} backends={data.backends} />
          </section>

          <div className="two-up">
            <section aria-labelledby="conn-heading" className="card">
              <h2 id="conn-heading">Connection</h2>
              <Facts
                items={[
                  ['Address', data.remote_address],
                  ['Connected', `${formatRelative(data.connected_at, now)} (${formatDateTime(data.connected_at)})`],
                  ['Last message', formatRelative(data.last_seen, now)],
                  ['Last heartbeat', data.last_heartbeat_at ? formatRelative(data.last_heartbeat_at, now) : null],
                  ['Messages', `${data.frames_in} received, ${data.frames_out} sent`],
                ]}
              />
            </section>
            <section aria-labelledby="id-heading" className="card">
              <h2 id="id-heading">Identity</h2>
              {data.boot ? (
                <Facts
                  items={[
                    ['Vendor', data.boot.vendor],
                    ['Model', data.boot.model],
                    ['Serial number', data.boot.serial_number],
                    ['Firmware', data.boot.firmware_version],
                    ['ICCID', data.boot.iccid],
                    ['IMSI', data.boot.imsi],
                    ['Meter', [data.boot.meter_type, data.boot.meter_serial_number].filter(Boolean).join(' · ') || null],
                  ]}
                />
              ) : (
                <p className="muted">Waiting for the charger&apos;s BootNotification.</p>
              )}
            </section>
          </div>

          <section aria-labelledby="connectors-heading">
            <h2 id="connectors-heading">Connectors</h2>
            {data.connectors.length === 0 ? (
              <p className="empty">The charger has not reported a connector status yet.</p>
            ) : (
              <ul className="cards">
                {data.connectors.map((c) => (
                  <li key={c.connector_id} className="card">
                    <div className="muted small">{c.connector_id === 0 ? 'Charger as a whole' : `Connector ${c.connector_id}`}</div>
                    <ConnectorStatus status={c.status} />
                    {c.error_code && c.error_code !== 'NoError' && (
                      <div className="small">
                        <Chip tone="bad">{c.error_code}</Chip> {c.info}
                      </div>
                    )}
                    <div className="muted small">{formatRelative(c.updated_at, now)}</div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <Transactions detail={data} />
          <IdTable title="Reservations" rows={data.reservations} keys={keys} />
          <IdTable title="Charging profiles" rows={data.charging_profiles} keys={keys} />

          {Object.keys(data.id_table_stats).length > 0 && (
            <details className="stats">
              <summary>What the transaction id table has done</summary>
              <dl className="kv">
                {Object.entries(data.id_table_stats)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([name, count]) => (
                    <div key={name}>
                      <dt>{name.replace(/_/g, ' ')}</dt>
                      <dd>{count}</dd>
                    </div>
                  ))}
              </dl>
            </details>
          )}
        </>
      )}
    </section>
  )
}

export function ChargerDetail() {
  const { org, chargerId } = useParams()
  // Keyed, so going from one charger to another starts clean instead of briefly showing the old one
  return <ChargerView key={`${org}/${chargerId}`} org={org ?? ''} chargerId={chargerId ?? ''} />
}
