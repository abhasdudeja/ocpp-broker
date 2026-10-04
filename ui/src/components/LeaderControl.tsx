import { useState } from 'react'

import { ApiError, apiRequest, type BackendLink, type LeaderChanged } from '../api/client'
import { useAuth } from '../auth'

/**
 * Lets an operator hand the charger to a connected follower. Nothing is written to the configuration: the charger
 * goes back to the configured leader when it reconnects (or by itself after a failover, if the organization has
 * `leader_failback`). Left out when there is no follower that could take over.
 */
export function LeaderControl({ org, chargerId, backends, onChanged }: { org: string; chargerId: string; backends: BackendLink[]; onChanged: () => void }) {
  const { key, keyRejected } = useAuth()
  const [asking, setAsking] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const candidates = backends.filter((b) => b.role === 'follower' && b.connected)
  if (candidates.length === 0 && !done) return null

  async function change(target: string) {
    setBusy(true)
    setFailure(null)
    try {
      await apiRequest<LeaderChanged>('POST', `/api/chargers/${encodeURIComponent(org)}/${encodeURIComponent(chargerId)}/leader`, key ?? '', { backend: target })
      setDone(`${target} leads now.`)
      setAsking(null)
      onChanged()
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) keyRejected()
      setFailure(error instanceof Error ? error.message : 'Could not change the leader')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="leader-heading" className="card">
      <h2 id="leader-heading">Change the leader</h2>
      {done && <p role="status" className="banner ok">{done}</p>}
      {candidates.length > 0 && (
        <ul className="inline-list">
          {candidates.map((b) => (
            <li key={b.key}>
              <button type="button" className="secondary" disabled={busy || asking !== null} onClick={() => { setAsking(b.key); setFailure(null); setDone(null) }}>
                Make {b.key} the leader
              </button>
            </li>
          ))}
        </ul>
      )}
      {asking !== null && (
        <div role="group" aria-label={`Confirm ${asking}`}>
          <p>
            Make <strong>{asking}</strong> the leader of {chargerId}? From now on {asking} answers the charger. A message the current leader has not answered yet may time out at the charger, which then sends it again.
            This is not saved in the configuration.
          </p>
          <button type="button" disabled={busy} onClick={() => void change(asking)}>
            {busy ? 'Changing…' : `Make ${asking} the leader`}
          </button>{' '}
          <button type="button" className="secondary" disabled={busy} onClick={() => setAsking(null)}>
            Keep the current leader
          </button>
        </div>
      )}
      {failure && (
        <p role="alert" className="banner error">
          {failure}
        </p>
      )}
    </section>
  )
}
