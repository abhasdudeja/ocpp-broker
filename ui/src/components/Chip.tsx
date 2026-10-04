import type { ReactNode } from 'react'

export type Tone = 'ok' | 'info' | 'warn' | 'bad' | 'neutral'

/** A short label. The meaning is always in the text; the colour only helps. */
export function Chip({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`chip tone-chip-${tone}`} title={title}>
      {children}
    </span>
  )
}

/** OCPP 1.6 connector statuses. Unknown ones are shown as they are, in a neutral colour. */
export function connectorTone(status: string): Tone {
  switch (status) {
    case 'Available':
      return 'ok'
    case 'Charging':
      return 'info'
    case 'Faulted':
      return 'bad'
    case 'Unavailable':
      return 'warn'
    default:
      return 'neutral'
  }
}

export function ConnectorStatus({ status }: { status: string }) {
  return <Chip tone={connectorTone(status)}>{status}</Chip>
}
