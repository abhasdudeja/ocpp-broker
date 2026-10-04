import type { BackendLink } from '../api/client'
import { Chip } from './Chip'

function Link({ link }: { link: BackendLink }) {
  const label = link.local ? 'this broker' : link.url
  return (
    <li className={`node backend ${link.connected ? 'is-up' : 'is-down'}`}>
      <div className="node-head">
        <Chip tone={link.role === 'leader' ? 'info' : 'neutral'}>{link.role}</Chip>
        <strong>{link.key}</strong>
      </div>
      {label && <div className="muted small node-url">{label}</div>}
      <div className="node-state">
        <span className={`dot ${link.connected ? 'dot-ok' : 'dot-bad'}`} aria-hidden="true" />
        {link.connected ? 'Connected' : 'Not connected'}
        {link.down_for_seconds != null && <span className="muted small"> for {Math.round(link.down_for_seconds)} s</span>}
      </div>
      {link.buffered_frames > 0 && (
        <div>
          <Chip tone="warn" title="Charger messages held until this backend is reachable">
            {link.buffered_frames} message{link.buffered_frames === 1 ? '' : 's'} waiting
          </Chip>
        </div>
      )}
    </li>
  )
}

/** Charger, then the broker, then the backends it talks to for this charger: the leader first. */
export function Topology({ chargerId, mode, backends }: { chargerId: string; mode: 'broker' | 'relay'; backends: BackendLink[] }) {
  const followers = backends.filter((b) => b.role === 'follower')
  const describe = (list: BackendLink[]) => list.map((b) => `${b.role} ${b.key} (${b.connected ? 'connected' : 'not connected'})`).join(' and ')
  const summary =
    mode === 'relay'
      ? `${chargerId} is connected to the broker, which forwards to ${describe(backends)}.`
      : followers.length === 0
        ? `${chargerId} is connected to the broker, which answers it itself.`
        : `${chargerId} is connected to the broker, which answers it itself and sends copies to ${describe(followers)}.`
  const role = mode === 'relay' ? 'relays and observes' : followers.length === 0 ? 'answers the charger' : 'answers and copies to followers'
  return (
    <figure className="topology" aria-label="How this charger is connected">
      <div className="node charger">
        <strong>{chargerId}</strong>
        <span className="muted small">charger</span>
      </div>
      <span className="arrow" aria-hidden="true" />
      <div className="node broker">
        <strong>Broker</strong>
        <span className="muted small">{role}</span>
      </div>
      <span className="arrow" aria-hidden="true" />
      <ul className="backends" aria-label="Backends">
        {backends.map((link) => (
          <Link key={link.key} link={link} />
        ))}
      </ul>
      <figcaption className="visually-hidden">{summary}</figcaption>
    </figure>
  )
}
