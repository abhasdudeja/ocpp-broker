import { Link } from 'react-router-dom'

import { describeEvent, eventTone } from '../eventText'
import { useEvents } from '../events'
import { formatClock } from '../format'
import { chargerPath } from '../pages/Chargers'

/**
 * What has just happened, newest first, from the live event stream. ``org`` and ``chargerId`` narrow it to one
 * organization or charger (on a charger's page the charger's name is left out of each line).
 */
export function EventFeed({ org, chargerId, limit = 15, title = 'Recent events' }: { org?: string; chargerId?: string; limit?: number; title?: string }) {
  const { recent, status } = useEvents()
  const shown = recent.filter((e) => (org === undefined || e.org === org) && (chargerId === undefined || e.charger_id === chargerId)).slice(0, limit)
  return (
    <section aria-labelledby="feed-heading">
      <h2 id="feed-heading">{title}</h2>
      {shown.length === 0 ? (
        <p className="empty">
          {status === 'unavailable'
            ? 'This broker does not offer live events; the pages above refresh by themselves instead.'
            : 'Nothing has happened since this page was opened. Events appear here as they happen.'}
        </p>
      ) : (
        <ol className="feed">
          {shown.map((event) => (
            <li key={event.id}>
              <span className={`dot dot-${eventTone(event)}`} aria-hidden="true" />
              <time dateTime={event.time} className="muted small">
                {formatClock(new Date(event.time))}
              </time>{' '}
              {chargerId === undefined && event.org && event.charger_id && (
                <>
                  <Link to={chargerPath(event.org, event.charger_id)}>{event.charger_id}</Link>{' '}
                </>
              )}
              {describeEvent(event)}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
