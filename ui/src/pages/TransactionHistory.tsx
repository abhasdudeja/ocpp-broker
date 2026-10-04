import { useEffect, useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, apiGet, type TransactionDetail } from '../api/client'
import { useAuth } from '../auth'
import { Chip } from '../components/Chip'
import { MeterChart } from '../components/MeterChart'
import { formatDateTime } from '../format'
import { buildSeries, formatDuration, formatEnergy } from '../history'
import { useNow } from '../useNow'
import { usePolling } from '../usePolling'
import { chargerPath } from './Chargers'

const REFRESH_MS = 15_000 // a running transaction keeps gaining readings

export function TransactionHistory() {
  const { org = '', chargerId = '', transactionId = '' } = useParams()
  const { key, keyRejected } = useAuth()
  const now = useNow()
  const path = `/api/history/transactions/${encodeURIComponent(org)}/${encodeURIComponent(chargerId)}/${encodeURIComponent(transactionId)}`
  const { data, error } = usePolling((signal) => apiGet<TransactionDetail>(path, key ?? '', signal), REFRESH_MS, path)
  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) keyRejected()
  }, [error, keyRejected])

  const series = useMemo(() => buildSeries(data?.readings ?? []), [data])
  const missing = error instanceof ApiError && error.status === 404
  const tx = data?.transaction ?? null

  return (
    <section>
      <p className="crumb">
        <Link to="/history">History</Link> / Transaction #{transactionId}
      </p>
      <header className="page-head">
        <h1>
          Transaction #{transactionId}{' '}
          {tx && (tx.open ? <Chip tone="info">running</Chip> : <Chip tone="neutral">ended</Chip>)}
        </h1>
        <p className="muted small">
          <Link to={chargerPath(org, chargerId)}>{chargerId}</Link> · {org}
        </p>
      </header>

      {missing && <p className="empty">This transaction is not in the history. It may be older than the retention, or it was answered by a backend rather than by this broker.</p>}
      {error && !missing && (
        <p role="alert" className="banner error">
          {data ? 'Could not refresh: ' : 'Could not load: '}
          {error.message}
        </p>
      )}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && !data.available && <p className="empty">{data.reason ?? 'History is not available.'}</p>}

      {tx && (
        <>
          <dl className="kv card">
            {(
              [
                ['Connector', tx.connector_id],
                ['Id tag', tx.id_tag],
                ['Started', tx.started_at ? formatDateTime(tx.started_at) : null],
                ['Ended', tx.stopped_at ? formatDateTime(tx.stopped_at) : null],
                ['Duration', formatDuration(tx.started_at, tx.stopped_at, now)],
                ['Meter at start', tx.meter_start === null ? null : `${tx.meter_start} Wh`],
                ['Meter at end', tx.meter_stop === null ? null : `${tx.meter_stop} Wh`],
                ['Energy', tx.energy_wh === null ? null : formatEnergy(tx.energy_wh)],
                ['Stop reason', tx.stop_reason],
                ['Stopped with tag', tx.stop_id_tag && tx.stop_id_tag !== tx.id_tag ? tx.stop_id_tag : null],
              ] as Array<[string, string | number | null]>
            ).map(([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{value === null || value === undefined || value === '' ? <span className="muted">—</span> : value}</dd>
              </div>
            ))}
          </dl>

          <section aria-labelledby="readings-heading">
            <h2 id="readings-heading">Meter readings</h2>
            {data?.readings_truncated && <p className="banner warn">There are more readings than are shown here; the oldest are listed.</p>}
            <MeterChart key={series.map((s) => s.id).join('\n')} series={series} />
          </section>
        </>
      )}
    </section>
  )
}
