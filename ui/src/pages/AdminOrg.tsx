import { useCallback, useEffect, useId, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, apiGet, apiRequest, type AdminApplied, type AdminChange, type AdminConfig, type AdminPlan } from '../api/client'
import {
  DEFAULTS,
  DEFAULT_SUBPROTOCOL,
  draftFromOrg,
  draftProblems,
  emptyDraft,
  generatePassword,
  nextKey,
  remove,
  upsert,
  type Draft,
  type Tri,
} from '../admin'
import { useAuth } from '../auth'
import { Chip } from '../components/Chip'
import { usePolling } from '../usePolling'
import { AdminDisabled } from './Admin'

const triOptions = (yes: string, no: string, standard: string) => (
  <>
    <option value="">{standard}</option>
    <option value="true">{yes}</option>
    <option value="false">{no}</option>
  </>
)

function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: (id: string) => ReactNode }) {
  const id = useId()
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <p className="muted small field-hint">{hint}</p>}
    </div>
  )
}

/** What a check found, and the choice to disconnect the chargers that are connected now. */
function Review({
  plan,
  name,
  drop,
  onDrop,
  onReload,
}: {
  plan: AdminPlan
  name: string
  drop: boolean
  onDrop: (value: boolean) => void
  onReload: () => void
}) {
  const connected = plan.connected_chargers[name] ?? 0
  return (
    <section aria-labelledby="review-heading" className="card review">
      <h2 id="review-heading">{plan.ok ? 'What will change' : 'This cannot be applied'}</h2>
      {plan.errors.length > 0 && (
        <ul role="alert" className="banner error list">
          {plan.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      {plan.conflict && (
        <p>
          <button type="button" onClick={onReload}>
            Reload the configuration
          </button>
        </p>
      )}
      {plan.warnings.length > 0 && (
        <ul className="banner warn list" aria-label="Warnings">
          {plan.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {plan.ok && plan.changes.length === 0 && <p className="muted">Nothing differs from the configuration as it is.</p>}
      {plan.changes.map((change) => (
        <div key={change.org}>
          <h3>
            {change.org} <Chip tone={change.kind === 'removed' ? 'bad' : change.kind === 'added' ? 'ok' : 'info'}>{change.kind}</Chip>
          </h3>
          <ul className="plain mono">
            {change.lines.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      ))}
      {plan.ok && plan.changes.length > 0 && (
        <>
          <p className="small">
            Chargers that connect from now on get these settings.
            {connected > 0 ? ` ${connected} charger${connected === 1 ? ' is' : 's are'} connected to ${name} now and keep what they connected with until they reconnect.` : ''}
          </p>
          {connected > 0 && (
            <label className="check">
              <input type="checkbox" checked={drop} onChange={(e) => onDrop(e.target.checked)} />
              Disconnect those {connected} charger{connected === 1 ? '' : 's'} now, so they reconnect with the new settings
            </label>
          )}
        </>
      )}
    </section>
  )
}

function Editor({ config, existing, reload }: { config: AdminConfig; existing: string | null; reload: () => void }) {
  const { key, keyRejected } = useAuth()
  const navigate = useNavigate()
  const original = existing === null ? undefined : config.organizations.find((o) => o.name === existing)
  const isNew = existing === null
  const [draft, setDraft] = useState<Draft>(() => (original ? draftFromOrg(original) : emptyDraft()))
  const [removing, setRemoving] = useState(false)
  const [plan, setPlan] = useState<AdminPlan | null>(null)
  const [drop, setDrop] = useState(false)
  const [reveal, setReveal] = useState<Record<number, boolean>>({})
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const name = isNew ? draft.name.trim() : (existing ?? '')

  const changes: AdminChange[] = removing ? [remove(name)] : [upsert(draft)]
  const problems = removing ? [] : draftProblems(draft, isNew)

  function edit(patch: Partial<Draft>) {
    setDraft((d) => ({ ...d, ...patch }))
    setPlan(null)
    setFailure(null)
  }

  async function call<T>(path: string, body: unknown): Promise<T | null> {
    setBusy(true)
    setFailure(null)
    try {
      return (await apiRequest<T>('POST', path, key ?? '', body)).body
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) keyRejected()
      setFailure(error instanceof Error ? error.message : 'The request failed')
      return null
    } finally {
      setBusy(false)
    }
  }

  async function check() {
    const answer = await call<AdminPlan>('/api/admin/config/validate', { revision: config.revision, changes })
    if (answer) setPlan(answer) // editing the form clears it, so a check on the screen is always of the form on the screen
  }

  async function apply() {
    const answer = await call<AdminApplied>('/api/admin/config/apply', { revision: config.revision, changes, drop_connections: drop ? [name] : [] })
    if (answer) void navigate('/admin', { state: { applied: answer } })
  }

  const setBackend = (index: number, patch: Partial<Draft['backends'][number]>) =>
    edit({ backends: draft.backends.map((b, i) => (i === index ? { ...b, ...patch } : b)) })
  const setCredential = (index: number, patch: Partial<Draft['credentials'][number]>) =>
    edit({ credentials: draft.credentials.map((c, i) => (i === index ? { ...c, ...patch } : c)) })

  if (!isNew && !original) {
    return (
      <p role="alert" className="banner warn">
        There is no organization named {existing} in the configuration. <Link to="/admin">Back to the list</Link>
      </p>
    )
  }

  const connectedHere = plan?.connected_chargers[name] ?? 0
  return (
    <form
      aria-label={isNew ? 'New organization' : `Organization ${existing}`}
      onSubmit={(event) => {
        event.preventDefault()
        void check()
      }}
    >
      {removing ? (
        <section className="card" aria-labelledby="remove-heading">
          <h2 id="remove-heading">Remove {name}</h2>
          <p>
            This takes <strong>{name}</strong> out of the configuration, with its {original?.credentials.length ?? 0} credential(s) and the {original?.tags ?? 0} tag(s) written in the file.
            Chargers of {name} that connect later are refused. Tags kept in MongoDB are not touched.
          </p>
          <button type="button" className="secondary" onClick={() => { setRemoving(false); setPlan(null) }}>
            Keep it
          </button>
        </section>
      ) : (
        <>
          <section className="card" aria-labelledby="basics-heading">
            <h2 id="basics-heading">Organization</h2>
            <Field label="Name" hint={isNew ? 'Part of the address chargers connect to: letters, digits, dots, dashes and underscores. It cannot be changed later.' : 'The name cannot be changed: add a new organization and remove this one.'}>
              {(id) => <input id={id} type="text" value={draft.name} readOnly={!isNew} onChange={(e) => edit({ name: e.target.value })} autoComplete="off" spellCheck={false} />}
            </Field>
            <label className="check">
              <input type="checkbox" checked={draft.connect_to_backend} onChange={(e) => edit({ connect_to_backend: e.target.checked })} />
              Connect chargers to backends
            </label>
            <p className="muted small field-hint">Off: the broker answers its chargers itself and the backends below are ignored.</p>
            <Field label="OCPP subprotocol" hint={`Empty means ${DEFAULT_SUBPROTOCOL}.`}>
              {(id) => <input id={id} type="text" value={draft.ocpp_subprotocol} placeholder={DEFAULT_SUBPROTOCOL} onChange={(e) => edit({ ocpp_subprotocol: e.target.value })} autoComplete="off" spellCheck={false} />}
            </Field>
          </section>

          <section className="card" aria-labelledby="backends-heading">
            <h2 id="backends-heading">Backends</h2>
            {draft.backends.length === 0 && <p className="muted">No backend. {draft.connect_to_backend ? 'Chargers of this organization connect to nothing until one is added.' : ''}</p>}
            {draft.backends.map((b, i) => (
              <fieldset key={b.key} className="row-group" aria-label={`Backend ${i + 1}`}>
                <div className="row">
                  <label className="check">
                    <input type="checkbox" checked={b.local} onChange={(e) => setBackend(i, { local: e.target.checked, url: e.target.checked ? '' : b.url })} aria-label={`Backend ${i + 1} is this broker`} />
                    This broker
                  </label>
                  <input type="text" aria-label={`Backend ${i + 1} id`} placeholder="id" value={b.id} onChange={(e) => setBackend(i, { id: e.target.value })} autoComplete="off" spellCheck={false} />
                  <input
                    type="text"
                    aria-label={`Backend ${i + 1} address`}
                    placeholder={b.local ? 'this broker answers' : 'ws://backend.example.com/ocpp'}
                    value={b.url}
                    disabled={b.local}
                    onChange={(e) => setBackend(i, { url: e.target.value })}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <label className="check">
                    <input
                      type="radio"
                      name="leader"
                      checked={b.leader}
                      aria-label={`Backend ${i + 1} leads`}
                      onChange={() => edit({ backends: draft.backends.map((x, n) => ({ ...x, leader: n === i })) })}
                    />
                    Leader
                  </label>
                  <button type="button" className="secondary" aria-label={`Remove backend ${i + 1}`} onClick={() => edit({ backends: draft.backends.filter((_, n) => n !== i) })}>
                    Remove
                  </button>
                </div>
              </fieldset>
            ))}
            <p>
              <button type="button" className="secondary" onClick={() => edit({ backends: [...draft.backends, { key: nextKey(), id: '', url: '', local: false, leader: draft.backends.length === 0, ocpp_subprotocol: '' }] })}>
                Add a backend
              </button>
            </p>
            <p className="muted small">The leader answers the charger; the others receive copies. With &quot;this broker&quot; as a backend, the broker answers if it leads, and takes over from the leader if it does not.</p>
          </section>

          <details className="card">
            <summary>Relay tuning</summary>
            <Field label="Held frames" hint={`Charger messages kept while the leader is away. Empty means ${DEFAULTS.backend_buffer_size}.`}>
              {(id) => <input id={id} type="text" inputMode="numeric" value={draft.backend_buffer_size} onChange={(e) => edit({ backend_buffer_size: e.target.value })} />}
            </Field>
            <Field label="Outage timeout" hint={`Seconds before a held message is answered with an error. Empty means ${DEFAULTS.backend_outage_timeout}.`}>
              {(id) => <input id={id} type="text" inputMode="numeric" value={draft.backend_outage_timeout} onChange={(e) => edit({ backend_outage_timeout: e.target.value })} />}
            </Field>
            <Field label="Failover timeout" hint={`Seconds the leader may be down before a follower takes over; 0 never. Empty means ${DEFAULTS.leader_failover_timeout}.`}>
              {(id) => <input id={id} type="text" inputMode="numeric" value={draft.leader_failover_timeout} onChange={(e) => edit({ leader_failover_timeout: e.target.value })} />}
            </Field>
            <Field label="Give the charger back to the configured leader" hint="After a failover, once the configured leader has been connected for the delay below. Default: off. A leader chosen by an operator is never taken away.">
              {(id) => (
                <select id={id} value={draft.leader_failback} onChange={(e) => edit({ leader_failback: e.target.value as Tri })}>
                  {triOptions('On', 'Off', 'Default (off)')}
                </select>
              )}
            </Field>
            <Field label="Fail-back delay" hint={`Seconds the configured leader must stay connected without a break. Empty means ${DEFAULTS.leader_failback_delay}.`}>
              {(id) => <input id={id} type="text" inputMode="decimal" value={draft.leader_failback_delay} onChange={(e) => edit({ leader_failback_delay: e.target.value })} />}
            </Field>
            <Field label="Transaction id mapping" hint="Give each backend its own transaction ids. By default on when there are several backends.">
              {(id) => (
                <select id={id} value={draft.mapping} onChange={(e) => edit({ mapping: e.target.value as Tri })}>
                  {triOptions('On', 'Off', 'Default')}
                </select>
              )}
            </Field>
            <Field label="Follower wait" hint="Seconds to wait for a follower's transaction id. Empty means the default.">
              {(id) => <input id={id} type="text" inputMode="decimal" value={draft.follower_wait} onChange={(e) => edit({ follower_wait: e.target.value })} />}
            </Field>
            <Field label="Answer repeated starts" hint="Answer a repeated StartTransaction from the stored result.">
              {(id) => (
                <select id={id} value={draft.dedupe_start} onChange={(e) => edit({ dedupe_start: e.target.value as Tri })}>
                  {triOptions('On', 'Off', 'Default')}
                </select>
              )}
            </Field>
            <Field label="Keep ended transactions" hint="Seconds the id table keeps an ended transaction. Empty means the default.">
              {(id) => <input id={id} type="text" inputMode="decimal" value={draft.retain_closed} onChange={(e) => edit({ retain_closed: e.target.value })} />}
            </Field>
            <Field label="Keep open transactions" hint="Seconds the id table keeps a transaction that never ended. Empty means the default.">
              {(id) => <input id={id} type="text" inputMode="decimal" value={draft.retain_open} onChange={(e) => edit({ retain_open: e.target.value })} />}
            </Field>
          </details>

          <section className="card" aria-labelledby="auth-heading">
            <h2 id="auth-heading">Chargers</h2>
            <Field label="Chargers must sign in" hint="With credentials listed, chargers must send them unless you say otherwise. With none listed, any charger may connect.">
              {(id) => (
                <select id={id} value={draft.charger_auth_required} onChange={(e) => edit({ charger_auth_required: e.target.value as Tri })}>
                  {triOptions('Always', 'Never', 'When credentials are listed')}
                </select>
              )}
            </Field>
            {draft.credentials.length > 0 && (
              <div className="table-wrap">
                <table className="table">
                  <caption className="visually-hidden">Charger credentials</caption>
                  <thead>
                    <tr>
                      <th scope="col">Charger id</th>
                      <th scope="col">Password</th>
                      <th scope="col">New password</th>
                      <th scope="col">
                        <span className="visually-hidden">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft.credentials.map((c, i) => {
                      const label = c.charger_id || `credential ${i + 1}`
                      return (
                        <tr key={c.key}>
                          <td>
                            <input type="text" aria-label={`Charger id of ${label}`} value={c.charger_id} readOnly={c.stored !== null} onChange={(e) => setCredential(i, { charger_id: e.target.value })} autoComplete="off" spellCheck={false} />
                          </td>
                          <td>
                            {c.stored === 'hash' && <Chip tone="ok">stored as a hash</Chip>}
                            {c.stored === 'plaintext' && <Chip tone="warn">stored as plaintext</Chip>}
                            {c.stored === null && <Chip tone="info">new</Chip>}
                          </td>
                          <td>
                            <input
                              type={reveal[c.key] ? 'text' : 'password'}
                              aria-label={`New password for ${label}`}
                              placeholder={c.stored ? 'keep the current one' : 'required'}
                              value={c.password}
                              onChange={(e) => setCredential(i, { password: e.target.value })}
                              autoComplete="new-password"
                              spellCheck={false}
                            />
                            {reveal[c.key] && c.password !== '' && <p className="small">Copy it now: it is hashed when you apply and cannot be shown again.</p>}
                          </td>
                          <td>
                            <button
                              type="button"
                              className="secondary"
                              aria-label={`Generate a password for ${label}`}
                              onClick={() => {
                                setCredential(i, { password: generatePassword() })
                                setReveal((r) => ({ ...r, [c.key]: true }))
                              }}
                            >
                              Generate
                            </button>{' '}
                            <button type="button" className="secondary" aria-label={`Remove ${label}`} onClick={() => edit({ credentials: draft.credentials.filter((_, n) => n !== i) })}>
                              Remove
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p>
              <button type="button" className="secondary" onClick={() => edit({ credentials: [...draft.credentials, { key: nextKey(), charger_id: '', stored: null, password: '' }] })}>
                Add a charger
              </button>
            </p>
            <p className="muted small">A password is sent once, hashed by the broker and never shown again. Leave the field empty to keep the current one. A charger that is not listed cannot connect.</p>
          </section>
        </>
      )}

      {problems.length > 0 && (
        <ul className="banner warn list" aria-label="To fix first">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {failure && (
        <p role="alert" className="banner error">
          {failure}
        </p>
      )}

      {plan && <Review plan={plan} name={name} drop={drop} onDrop={setDrop} onReload={reload} />}

      <p className="actions">
        <button type="submit" disabled={busy || problems.length > 0}>
          {busy ? 'Working…' : 'Check changes'}
        </button>{' '}
        <button type="button" disabled={busy || !plan?.ok || plan.changes.length === 0} onClick={() => void apply()}>
          {removing ? `Remove ${name}` : isNew ? 'Add organization' : 'Apply changes'}
          {drop && connectedHere > 0 ? ' and disconnect chargers' : ''}
        </button>{' '}
        {!isNew && !removing && (
          <button type="button" className="secondary danger" onClick={() => { setRemoving(true); setPlan(null) }}>
            Remove organization…
          </button>
        )}{' '}
        <Link to="/admin" className="button secondary">
          Cancel
        </Link>
      </p>
    </form>
  )
}

export function AdminOrg() {
  const { name } = useParams()
  const { key, keyRejected } = useAuth()
  const existing = name === undefined ? null : name
  const [config, setConfig] = useState<AdminConfig | null>(null)
  const [failure, setFailure] = useState<ApiError | Error | null>(null)
  const [round, setRound] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    apiGet<AdminConfig>('/api/admin/config', key ?? '', controller.signal)
      .then((loaded) => {
        setConfig(loaded)
        setFailure(null)
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        if (error instanceof ApiError && error.status === 401) keyRejected()
        setFailure(error instanceof Error ? error : new Error(String(error)))
      })
    return () => controller.abort()
  }, [key, keyRejected, round])

  // The file may be changed by someone else while this form is open: say so, so a check is not made on stale ground
  const watch = usePolling((signal) => apiGet<AdminConfig>('/api/admin/config', key ?? '', signal), 15_000, `${round}`)
  const stale = config !== null && watch.data !== null && watch.data.revision !== config.revision
  const reload = useCallback(() => setRound((n) => n + 1), [])

  if (failure instanceof ApiError && failure.status === 403) return <AdminDisabled />

  return (
    <section>
      <p className="crumb">
        <Link to="/admin">← Admin</Link>
      </p>
      <header className="page-head">
        <h1>{existing === null ? 'Add an organization' : existing}</h1>
      </header>
      {failure && (
        <p role="alert" className="banner error">
          Could not load: {failure.message}
        </p>
      )}
      {!config && !failure && <p className="muted">Loading…</p>}
      {config && !config.writable && (
        <p role="alert" className="banner warn">
          {config.writable_reason ?? 'The configuration cannot be changed.'}
        </p>
      )}
      {stale && (
        <p role="alert" className="banner warn">
          The configuration file has changed since this page loaded it. <button type="button" onClick={reload}>Reload</button> to start from what is there now (your edits on this page are lost).
        </p>
      )}
      {config && config.writable && <Editor key={`${config.revision}-${round}`} config={config} existing={existing} reload={reload} />}
    </section>
  )
}
