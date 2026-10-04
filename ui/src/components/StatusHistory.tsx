import { Link } from 'react-router-dom'

import { apiGet, withQuery, type StatusPage } from '../api/client'
import { useAuth } from '../auth'
import { useRefreshOnEvents } from '../events'
import { formatDateTime } from '../format'
import { usePolling } from '../usePolling'
import { ConnectorStatus, Chip } from './Chip'

const SHOWN = 10
// A status is written to MongoDB just after the charger is answered: wait for it before asking again
const AFTER_EVENT_MS = 1500

/**
 * The latest connector status changes of one charger, from the history. Left out entirely when there is no
 * history (no MongoDB, or it is not answering): the live connector cards above already say what is true now.
 */
export function StatusHistory({ org, chargerId }: { org: string; chargerId: string }) {
  const { key } = useAuth()
  const { data, reload } = usePolling(
    (signal) => apiGet<StatusPage>(withQuery('/api/history/statuses', { org, charger_id: chargerId, limit: String(SHOWN) }), key ?? '', signal),
    60_000,
    `${org}/${chargerId}`,
  )
  useRefreshOnEvents(reload, (e) => e.type === 'charger.status' && e.org === org && e.charger_id === chargerId, AFTER_EVENT_MS)
  if (!data || !data.available) return null

  const more = `/history?${new URLSearchParams({ tab: 'statuses', org, charger: chargerId })}`
  return (
    <section aria-labelledby="status-history-heading">
      <h2 id="status-history-heading">Recent status changes</h2>
      {data.items.length === 0 ? (
        <p className="empty">No status change has been recorded for this charger.</p>
      ) : (
        <>
          <ol className="feed">
            {data.items.map((s, i) => (
              <li key={`${s.timestamp}-${s.connector_id}-${i}`}>
                <span className="muted small">{s.timestamp ? formatDateTime(s.timestamp) : '—'}</span>{' '}
                <span className="muted small">{s.connector_id === 0 ? 'charger' : `#${s.connector_id}`}</span> <ConnectorStatus status={s.status} />
                {s.error_code && s.error_code !== 'NoError' && (
                  <>
                    {' '}
                    <Chip tone="bad">{s.error_code}</Chip>
                  </>
                )}
              </li>
            ))}
          </ol>
          <p className="small">
            <Link to={more}>All status changes of this charger</Link>
          </p>
        </>
      )}
    </section>
  )
}
