import { useId, useState } from 'react'

import { ApiError, apiRequest, type Tag, type TagAck, type TagStatusValue, type TagTypeValue, type TagValidation } from '../api/client'
import { useAuth } from '../auth'

export const TAG_STATUSES: TagStatusValue[] = ['Accepted', 'Blocked', 'Expired', 'Invalid', 'ConcurrentTx']
export const TAG_TYPES: TagTypeValue[] = ['RFID', 'NFC', 'QRCode', 'MobileApp', 'UserId']

/** The tag as the API wants it: empty optional fields are left out. */
export function tagPayload(fields: { id_tag: string; status: TagStatusValue; tag_type: TagTypeValue; expiry_date: string; parent_id_tag: string; description: string }, previous: Tag | null): Tag {
  const tag: Tag = { id_tag: fields.id_tag.trim(), status: fields.status, tag_type: fields.tag_type }
  if (fields.expiry_date.trim()) tag.expiry_date = fields.expiry_date.trim()
  if (fields.parent_id_tag.trim()) tag.parent_id_tag = fields.parent_id_tag.trim()
  if (fields.description.trim()) tag.description = fields.description.trim()
  if (previous?.metadata) tag.metadata = previous.metadata // editing must not lose what the form does not show
  return tag
}

/** Add a tag, or change one (``initial``). The broker's own rules are asked first, and shown, before anything is saved. */
export function TagForm({ org, initial, onSaved, onCancel }: { org: string; initial: Tag | null; onSaved: (message: string) => void; onCancel: () => void }) {
  const { key, keyRejected } = useAuth()
  const uid = useId()
  const editing = initial !== null
  const [idTag, setIdTag] = useState(initial?.id_tag ?? '')
  const [status, setStatus] = useState<TagStatusValue>(initial?.status ?? 'Accepted')
  const [tagType, setTagType] = useState<TagTypeValue>(initial?.tag_type ?? 'RFID')
  const [expiry, setExpiry] = useState(initial?.expiry_date ?? '')
  const [parent, setParent] = useState(initial?.parent_id_tag ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [problems, setProblems] = useState<string[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit() {
    setProblems([])
    setWarnings([])
    setFailure(null)
    if (!idTag.trim()) {
      setProblems(['The id tag is required.'])
      return
    }
    const tag = tagPayload({ id_tag: idTag, status, tag_type: tagType, expiry_date: expiry, parent_id_tag: parent, description }, initial)
    const base = `/api/tags/organizations/${encodeURIComponent(org)}/tags`
    setBusy(true)
    try {
      const checked = await apiRequest<TagValidation>('POST', `${base}/validate${editing ? '?for_update=true' : ''}`, key ?? '', tag)
      setWarnings(checked.body.warnings ?? [])
      if (!checked.body.is_valid) {
        setProblems(checked.body.errors ?? [])
        return
      }
      const saved = editing
        ? await apiRequest<TagAck>('PUT', `${base}/${encodeURIComponent(tag.id_tag)}`, key ?? '', tag)
        : await apiRequest<TagAck>('POST', base, key ?? '', tag)
      onSaved(saved.body.message)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) keyRejected()
      setFailure(error instanceof Error ? error.message : 'Could not save the tag')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      className="card tag-form"
      aria-label={editing ? `Edit tag ${initial.id_tag}` : 'Add a tag'}
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <h3>{editing ? `Edit ${initial.id_tag}` : 'Add a tag'}</h3>
      <div className="field">
        <label htmlFor={`${uid}-id`}>Id tag</label>
        <input id={`${uid}-id`} type="text" value={idTag} readOnly={editing} onChange={(e) => setIdTag(e.target.value)} autoComplete="off" spellCheck={false} />
        <p className="muted small field-hint">Up to 20 characters. It is what the charger sends.</p>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-status`}>Status</label>
        <select id={`${uid}-status`} value={status} onChange={(e) => setStatus(e.target.value as TagStatusValue)}>
          {TAG_STATUSES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-type`}>Type</label>
        <select id={`${uid}-type`} value={tagType} onChange={(e) => setTagType(e.target.value as TagTypeValue)}>
          {TAG_TYPES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-expiry`}>Expires</label>
        <input id={`${uid}-expiry`} type="text" value={expiry} onChange={(e) => setExpiry(e.target.value)} autoComplete="off" spellCheck={false} />
        <p className="muted small field-hint">Optional, like 2027-01-31T00:00:00Z. After this time the broker answers Expired.</p>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-parent`}>Parent id tag</label>
        <input id={`${uid}-parent`} type="text" value={parent} onChange={(e) => setParent(e.target.value)} autoComplete="off" spellCheck={false} />
      </div>
      <div className="field">
        <label htmlFor={`${uid}-description`}>Description</label>
        <input id={`${uid}-description`} type="text" value={description} onChange={(e) => setDescription(e.target.value)} autoComplete="off" />
      </div>

      {problems.length > 0 && (
        <ul role="alert" className="banner error list">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {warnings.length > 0 && (
        <ul className="banner warn list" aria-label="Warnings">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {failure && (
        <p role="alert" className="banner error">
          {failure}
        </p>
      )}

      <button type="submit" disabled={busy}>
        {busy ? 'Saving…' : editing ? 'Save changes' : 'Add tag'}
      </button>{' '}
      <button type="button" className="secondary" onClick={onCancel} disabled={busy}>
        Cancel
      </button>
    </form>
  )
}
