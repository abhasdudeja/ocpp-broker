import { useEffect, useId, useRef, useState } from 'react'

import { ApiError, apiGet, apiPost, isAbort, type CommandHistory, type CommandLogEntry, type CommandSpec } from '../api/client'
import { useAuth } from '../auth'
import { useRefreshOnEvents } from '../events'
import { formatRelative } from '../format'
import { buildPayload, draftFromValue, emptyDraft, type Draft, type Errors, type JsonSchema } from '../schemaForm'
import { useNow } from '../useNow'
import { usePolling } from '../usePolling'
import { Chip, type Tone } from './Chip'
import { SchemaForm } from './SchemaForm'

const HISTORY_REFRESH_MS = 10_000
const DEFAULT_TIMEOUT = '30'

const pretty = (value: unknown): string => JSON.stringify(value, null, 2)
const schemaOf = (spec: CommandSpec): JsonSchema => spec.json_schema as JsonSchema

function statusTone(status: string): Tone {
  return status === 'success' ? 'ok' : status === 'pending' ? 'info' : status === 'timeout' || status === 'cancelled' ? 'warn' : 'bad'
}

const RISK_LABEL: Record<CommandSpec['risk'], string> = { read: 'Read-only', change: 'Changes settings', disruptive: 'Can interrupt the charger' }

interface Outcome {
  tone: 'ok' | 'warn' | 'bad'
  title: string
  body?: unknown
}

function describeFailure(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return 'This charger is no longer connected to this broker instance, so nothing was sent.'
    if (error.status === 422) return `The broker rejected the command and sent nothing: ${error.message}`
    if (error.status === 503) return `The charger could not be reached: ${error.message}`
    if (error.status === 0) return 'Could not reach the broker. The command may or may not have been sent; check the history below.'
    return `The broker answered with an error: ${error.message}`
  }
  return 'Something went wrong while sending the command.'
}

interface CommandRecord {
  message_id: string
  status: string
  response?: unknown
  error?: string | null
}

function CommandPanel({
  org,
  chargerId,
  initial,
  onSent,
}: {
  org: string
  chargerId: string
  initial: { action: string; payload: unknown } | null
  onSent: () => void
}) {
  const { key, keyRejected } = useAuth()
  const uid = useId()
  const [catalog, setCatalog] = useState<CommandSpec[] | null>(null)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [action, setAction] = useState(initial?.action ?? '')
  const [mode, setMode] = useState<'form' | 'json'>(initial ? 'json' : 'form')
  const [draft, setDraft] = useState<Draft>(undefined)
  const [jsonText, setJsonText] = useState(initial ? pretty(initial.payload) : '{}')
  const [timeout, setTimeoutText] = useState(DEFAULT_TIMEOUT)
  const [errors, setErrors] = useState<Errors>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [phase, setPhase] = useState<'idle' | 'confirm' | 'sending'>('idle')
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => {
    const abort = new AbortController()
    apiGet<{ commands: CommandSpec[] }>('/api/ocpp/commands/catalog', key ?? '', abort.signal).then(
      (body) => setCatalog(body.commands),
      (error: unknown) => {
        if (isAbort(error)) return
        if (error instanceof ApiError && error.status === 401) keyRejected()
        setCatalogError(error instanceof Error ? error.message : 'Could not load the command list')
      },
    )
    return () => abort.abort()
  }, [key, keyRejected])

  useEffect(() => () => controller.current?.abort(), [])

  const spec = catalog?.find((c) => c.action === action) ?? null
  const schema = spec ? schemaOf(spec) : null

  function choose(next: string) {
    setAction(next)
    setErrors({})
    setNotice(null)
    setOutcome(null)
    setPhase('idle')
    const chosen = catalog?.find((c) => c.action === next)
    setDraft(chosen ? emptyDraft(schemaOf(chosen), true) : undefined)
    setJsonText('{}')
  }

  function showJson() {
    if (schema) {
      const built = buildPayload(schema, draft)
      setJsonText(pretty(built.value ?? {}))
    }
    setNotice(null)
    setMode('json')
  }

  function showForm() {
    if (!schema) return
    let parsed: unknown
    try {
      parsed = JSON.parse(jsonText)
    } catch {
      setNotice('The JSON is not valid, so it cannot be shown as a form. Fix it or keep editing it as JSON.')
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setNotice('A command payload is a JSON object, like {"connectorId": 1}.')
      return
    }
    setDraft(draftFromValue(schema, parsed))
    setErrors({})
    setNotice(null)
    setMode('form')
  }

  /** The payload to send, or null after showing what is wrong. */
  function payloadToSend(): Record<string, unknown> | null {
    setNotice(null)
    setErrors({})
    if (mode === 'json') {
      try {
        const parsed: unknown = JSON.parse(jsonText)
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed as Record<string, unknown>
        setNotice('A command payload is a JSON object, like {"connectorId": 1}.')
      } catch {
        setNotice('The JSON is not valid.')
      }
      return null
    }
    if (!schema) return null
    const built = buildPayload(schema, draft)
    if (Object.keys(built.errors).length > 0) {
      setErrors(built.errors)
      setNotice('Some fields need attention.')
      return null
    }
    return (built.value ?? {}) as Record<string, unknown>
  }

  function timeoutSeconds(): number | null {
    const n = Number(timeout)
    if (/^\d+$/.test(timeout.trim()) && n >= 1 && n <= 300) return n
    setNotice('The time to wait for the charger is 1 to 300 seconds.')
    return null
  }

  function review() {
    if (!spec || payloadToSend() === null || timeoutSeconds() === null) return
    if (spec.risk === 'disruptive') setPhase('confirm')
    else void send()
  }

  async function send() {
    if (!spec) return
    const payload = payloadToSend()
    const seconds = timeoutSeconds()
    if (payload === null || seconds === null) {
      setPhase('idle')
      return
    }
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    setPhase('sending')
    setOutcome(null)
    try {
      const { body } = await apiPost<CommandRecord>(
        `/api/ocpp/organizations/${encodeURIComponent(org)}/chargers/${encodeURIComponent(chargerId)}/commands`,
        key ?? '',
        { action: spec.action, payload, timeout: seconds },
        { signal: abort.signal, accept: [504] },
      )
      if (body.status === 'success') setOutcome({ tone: 'ok', title: `${spec.action}: the charger answered`, body: body.response })
      else if (body.status === 'timeout') setOutcome({ tone: 'warn', title: `${spec.action}: no answer within ${seconds} s`, body: body.error })
      else setOutcome({ tone: 'bad', title: `${spec.action}: the charger refused it`, body: body.error })
    } catch (error) {
      if (isAbort(error)) return
      if (error instanceof ApiError && error.status === 401) keyRejected()
      setOutcome({ tone: 'bad', title: describeFailure(error) })
    } finally {
      if (controller.current === abort) {
        setPhase('idle')
        onSent()
      }
    }
  }

  if (catalogError) {
    return (
      <p role="alert" className="banner error">
        Could not load the command list: {catalogError}
      </p>
    )
  }
  if (!catalog) return <p className="muted">Loading commands…</p>

  const groups: Array<[CommandSpec['risk'], string]> = [
    ['read', RISK_LABEL.read],
    ['change', RISK_LABEL.change],
    ['disruptive', RISK_LABEL.disruptive],
  ]

  return (
    <form
      className="card command-panel"
      aria-label="Send a command"
      onSubmit={(event) => {
        event.preventDefault()
        if (phase === 'idle') review()
      }}
    >
      <div className="field">
        <label htmlFor={`${uid}-action`}>Command</label>
        <select id={`${uid}-action`} value={action} onChange={(e) => choose(e.target.value)} disabled={phase === 'sending'}>
          <option value="">Choose a command…</option>
          {groups.map(([risk, label]) => (
            <optgroup key={risk} label={label}>
              {catalog
                .filter((c) => c.risk === risk)
                .map((c) => (
                  <option key={c.action} value={c.action}>
                    {c.action}
                  </option>
                ))}
            </optgroup>
          ))}
          {action !== '' && !spec && <option value={action}>{action}</option>}
        </select>
      </div>

      {spec && schema && (
        <>
          <p className="small">
            {spec.summary}. <Chip tone={spec.risk === 'disruptive' ? 'warn' : 'neutral'}>{RISK_LABEL[spec.risk]}</Chip>
          </p>

          <div className="mode" role="group" aria-label="How to enter the payload">
            <button type="button" className="secondary" aria-pressed={mode === 'form'} onClick={showForm} disabled={phase === 'sending'}>
              Form
            </button>
            <button type="button" className="secondary" aria-pressed={mode === 'json'} onClick={showJson} disabled={phase === 'sending'}>
              JSON
            </button>
          </div>

          {mode === 'form' ? (
            <SchemaForm schema={schema} draft={draft} onChange={setDraft} errors={errors} idPrefix={uid.replace(/:/g, '')} />
          ) : (
            <div className="field">
              <label htmlFor={`${uid}-json`}>Payload (JSON)</label>
              <textarea
                id={`${uid}-json`}
                className="json-input"
                rows={8}
                spellCheck={false}
                value={jsonText}
                onChange={(e) => setJsonText(e.target.value)}
                aria-describedby={`${uid}-json-hint`}
              />
              <p id={`${uid}-json-hint`} className="muted small field-hint">
                Sent exactly as written; it is not checked against the command&apos;s schema.
              </p>
            </div>
          )}

          <div className="field">
            <label htmlFor={`${uid}-timeout`}>Wait for the charger up to (seconds)</label>
            <input id={`${uid}-timeout`} type="text" inputMode="numeric" value={timeout} onChange={(e) => setTimeoutText(e.target.value)} className="narrow" />
          </div>

          {notice && (
            <p role="alert" className="banner warn">
              {notice}
            </p>
          )}

          {phase === 'confirm' ? (
            <div className="confirm" role="alertdialog" aria-label={`Confirm ${spec.action}`}>
              <p>
                <strong>
                  Send {spec.action} to {chargerId}?
                </strong>{' '}
                {spec.summary}. This can interrupt a charging session or take the charger out of service.
              </p>
              <button type="button" onClick={() => void send()}>
                Yes, send {spec.action}
              </button>{' '}
              <button type="button" className="secondary" onClick={() => setPhase('idle')}>
                Cancel
              </button>
            </div>
          ) : (
            <button type="submit" disabled={phase === 'sending'}>
              {phase === 'sending' ? 'Waiting for the charger…' : `Send ${spec.action}`}
            </button>
          )}
        </>
      )}

      {outcome && (
        <div role="status" aria-label="Result of the command" className={`outcome outcome-${outcome.tone}`}>
          <strong>{outcome.title}</strong>
          {outcome.body !== undefined && outcome.body !== null && <pre>{typeof outcome.body === 'string' ? outcome.body : pretty(outcome.body)}</pre>}
        </div>
      )}
    </form>
  )
}

function History({ entries, onReuse }: { entries: CommandLogEntry[]; onReuse: (entry: CommandLogEntry) => void }) {
  const now = useNow()
  if (entries.length === 0) return <p className="empty">No commands have been sent to this charger since it connected.</p>
  return (
    <ol className="history">
      {entries.map((entry) => (
        <li key={entry.message_id}>
          <div className="history-head">
            <strong>{entry.action}</strong> <Chip tone={statusTone(entry.status)}>{entry.status}</Chip>{' '}
            <span className="muted small">
              {formatRelative(entry.sent_at, now)}
              {entry.duration_ms !== null && ` · took ${entry.duration_ms} ms`}
            </span>
          </div>
          {entry.error && <p className="small">{entry.error}</p>}
          <details>
            <summary>Details</summary>
            <p className="small muted">Sent</p>
            <pre>{pretty(entry.payload)}</pre>
            {entry.response !== null && entry.response !== undefined && (
              <>
                <p className="small muted">Answer</p>
                <pre>{pretty(entry.response)}</pre>
              </>
            )}
            <p className="small muted">
              Message id <code>{entry.message_id}</code>
            </p>
            <button type="button" className="secondary" onClick={() => onReuse(entry)}>
              Use again
            </button>
          </details>
        </li>
      ))}
    </ol>
  )
}

/** Send a command to this charger and see what was sent before. */
export function Commands({ org, chargerId }: { org: string; chargerId: string }) {
  const { key } = useAuth()
  const [again, setAgain] = useState<{ action: string; payload: unknown; nonce: number } | null>(null)
  const history = usePolling(
    (signal) => apiGet<CommandHistory>(`/api/chargers/${encodeURIComponent(org)}/${encodeURIComponent(chargerId)}/commands`, key ?? '', signal),
    HISTORY_REFRESH_MS,
  )
  useRefreshOnEvents(history.reload, (e) => e.type === 'command.result' && e.org === org && e.charger_id === chargerId, 100)

  return (
    <section aria-labelledby="commands-heading">
      <h2 id="commands-heading">Commands</h2>
      {/* keyed, so "use again" starts the panel over with that payload */}
      <CommandPanel key={again?.nonce ?? 0} org={org} chargerId={chargerId} initial={again} onSent={history.reload} />
      <h3>Sent to this charger</h3>
      <p className="muted small">The last 50 commands sent through the API or this console while the charger stayed connected. Secret values are hidden.</p>
      {history.error && !history.data ? (
        <p className="empty">Not available{history.error instanceof ApiError && history.error.status === 404 ? ' while the charger is not connected' : `: ${history.error.message}`}.</p>
      ) : history.data ? (
        <History entries={history.data.commands} onReuse={(entry) => setAgain({ action: entry.action, payload: entry.payload, nonce: (again?.nonce ?? 0) + 1 })} />
      ) : (
        <p className="muted">Loading…</p>
      )}
    </section>
  )
}
