import { useId, useState } from 'react'

import { ApiError, apiRequest, type TagImportResult } from '../api/client'
import { useAuth } from '../auth'

type Source = 'json' | 'csv'

export function ImportSummary({ result, checked }: { result: TagImportResult; checked: boolean }) {
  const verb = checked ? 'would be' : 'were'
  return (
    <div role="status" aria-label="Import result" className="outcome">
      <strong>
        {checked ? 'Checked, nothing has been changed yet.' : 'Imported.'} Of {result.total} record{result.total === 1 ? '' : 's'}: {result.imported} {verb} added, {result.updated} {verb} updated, {result.skipped}{' '}
        {verb} left alone (already there), {result.errors.length} rejected.
      </strong>
      {result.errors.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Record</th>
              <th scope="col">Id tag</th>
              <th scope="col">Problem</th>
            </tr>
          </thead>
          <tbody>
            {result.errors.map((e) => (
              <tr key={`${e.record}-${e.error}`}>
                <td>{e.record}</td>
                <td>{e.id_tag ?? <span className="muted">—</span>}</td>
                <td>{e.error}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/**
 * Import tags from JSON or CSV text (typed or read from a file). The broker checks first without changing
 * anything and the result is shown; only then can the import be applied.
 */
export function TagImport({ org, onImported, onClose }: { org: string; onImported: (result: TagImportResult) => void; onClose: () => void }) {
  const { key, keyRejected } = useAuth()
  const uid = useId()
  const [source, setSource] = useState<Source>('json')
  const [text, setText] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [checked, setChecked] = useState<{ result: TagImportResult; text: string; source: Source; overwrite: boolean } | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const upToDate = checked !== null && checked.text === text && checked.source === source && checked.overwrite === overwrite

  async function run(validateOnly: boolean) {
    setFailure(null)
    setBusy(true)
    try {
      const { body } = await apiRequest<TagImportResult>('POST', `/api/tags/organizations/${encodeURIComponent(org)}/tags/import`, key ?? '', {
        source,
        data: text,
        overwrite_existing: overwrite,
        validate_only: validateOnly,
      })
      if (validateOnly) setChecked({ result: body, text, source, overwrite })
      else onImported(body)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) keyRejected()
      setFailure(error instanceof ApiError && error.status === 400 ? `The text could not be read: ${error.message}` : error instanceof Error ? error.message : 'The import failed')
    } finally {
      setBusy(false)
    }
  }

  async function readFile(file: File | undefined) {
    if (!file) return
    setText(await file.text())
    if (file.name.toLowerCase().endsWith('.csv')) setSource('csv')
    else if (file.name.toLowerCase().endsWith('.json')) setSource('json')
  }

  return (
    <section className="card" aria-label="Import tags">
      <h3>Import tags</h3>
      <p className="muted small">
        Paste JSON (a list of tags, or an export from this console) or CSV, or choose a file. The broker checks it first and tells you what would happen; nothing changes until you
        import.
      </p>
      <div className="field">
        <label htmlFor={`${uid}-file`}>File</label>
        <input id={`${uid}-file`} type="file" accept=".json,.csv,text/csv,application/json" onChange={(e) => void readFile(e.target.files?.[0])} />
      </div>
      <div className="field">
        <label htmlFor={`${uid}-source`}>Format</label>
        <select id={`${uid}-source`} value={source} onChange={(e) => setSource(e.target.value as Source)}>
          <option value="json">JSON</option>
          <option value="csv">CSV</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-data`}>Tags</label>
        <textarea id={`${uid}-data`} className="json-input" rows={8} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      <div className="field">
        <label>
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} /> Replace tags that already exist (otherwise they are left alone)
        </label>
      </div>

      {failure && (
        <p role="alert" className="banner error">
          {failure}
        </p>
      )}
      {checked && <ImportSummary result={checked.result} checked />}
      {checked && !upToDate && <p className="muted small">The text or options changed since the check; check again before importing.</p>}

      <button type="button" className="secondary" disabled={busy || !text.trim()} onClick={() => void run(true)}>
        Check
      </button>{' '}
      <button type="button" disabled={busy || checked === null || !upToDate || checked.result.imported + checked.result.updated === 0} onClick={() => void run(false)}>
        Import
      </button>{' '}
      <button type="button" className="secondary" onClick={onClose} disabled={busy}>
        Close
      </button>
    </section>
  )
}

