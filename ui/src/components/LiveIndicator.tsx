import { useEvents, type StreamStatus } from '../events'

const LABELS: Record<StreamStatus, { text: string; title: string; tone: 'ok' | 'warn' | 'off' }> = {
  connecting: { text: 'Connecting…', title: 'Opening the live event stream', tone: 'off' },
  live: { text: 'Live', title: 'Changes appear as they happen', tone: 'ok' },
  reconnecting: { text: 'Reconnecting…', title: 'The live stream dropped; pages still refresh every few seconds', tone: 'warn' },
  unavailable: { text: 'Polling', title: 'This broker has no live event stream; pages refresh every few seconds', tone: 'off' },
}

/** Whether the console is receiving events as they happen. */
export function LiveIndicator() {
  const { status } = useEvents()
  const label = LABELS[status]
  return (
    <span className="live" role="status" title={label.title}>
      <span className={`dot dot-${label.tone === 'ok' ? 'ok' : label.tone === 'warn' ? 'warn' : 'off'}`} aria-hidden="true" />
      {label.text}
    </span>
  )
}
