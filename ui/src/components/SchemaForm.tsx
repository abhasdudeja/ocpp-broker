import { emptyDraft, kindOf, nowUtc, pathOf, type Draft, type Errors, type JsonSchema } from '../schemaForm'

interface FieldProps {
  name: string
  schema: JsonSchema
  draft: Draft
  required: boolean
  path: string
  errors: Errors
  idPrefix: string
  onChange: (draft: Draft) => void
}

const idFor = (prefix: string, path: string) => `${prefix}-${path.replace(/[^A-Za-z0-9]+/g, '-')}`

function hint(schema: JsonSchema): string | null {
  const bits: string[] = []
  if (schema.maxLength !== undefined) bits.push(`up to ${schema.maxLength} characters`)
  const { minimum, maximum } = schema
  if (minimum !== undefined && maximum !== undefined) bits.push(`${minimum} to ${maximum}`)
  else if (minimum !== undefined) bits.push(`${minimum} or more`)
  else if (maximum !== undefined) bits.push(`up to ${maximum}`)
  if (schema.format === 'date-time') bits.push('like 2026-10-04T12:00:00Z')
  if (schema.format === 'uri') bits.push('an address, for example ftp://host/file')
  return bits.length ? bits.join(', ') : null
}

function Field({ name, schema, draft, required, path, errors, idPrefix, onChange }: FieldProps) {
  const kind = kindOf(schema)
  const id = idFor(idPrefix, path)
  const error = errors[path]
  const note = hint(schema)
  const describedBy = [error ? `${id}-error` : null, note ? `${id}-hint` : null].filter(Boolean).join(' ') || undefined
  const label = (
    <label htmlFor={id}>
      {name}
      {required && (
        <span className="required" title="Required">
          {' '}
          *
        </span>
      )}
    </label>
  )
  const feedback = (
    <>
      {note && (
        <p id={`${id}-hint`} className="muted small field-hint">
          {note}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="field-error small">
          {error}
        </p>
      )}
    </>
  )

  if (kind === 'object') {
    const need = new Set(schema.required ?? [])
    return (
      <fieldset className="subform">
        <legend>
          {name}
          {required && <span className="required"> *</span>}
        </legend>
        {draft === undefined ? (
          <button type="button" className="secondary" onClick={() => onChange(emptyDraft(schema, true))}>
            Add {name}
          </button>
        ) : (
          <>
            {Object.entries(schema.properties ?? {}).map(([child, childSchema]) => (
              <Field
                key={child}
                name={child}
                schema={childSchema}
                draft={(draft as { [n: string]: Draft })[child]}
                required={need.has(child)}
                path={pathOf(path, child)}
                errors={errors}
                idPrefix={idPrefix}
                onChange={(next) => onChange({ ...(draft as { [n: string]: Draft }), [child]: next })}
              />
            ))}
            {!required && (
              <button type="button" className="secondary" onClick={() => onChange(undefined)}>
                Remove {name}
              </button>
            )}
          </>
        )}
        {error && <p className="field-error small">{error}</p>}
      </fieldset>
    )
  }

  if (kind === 'array') {
    const items = Array.isArray(draft) ? draft : null
    return (
      <fieldset className="subform">
        <legend>
          {name}
          {required && <span className="required"> *</span>}
        </legend>
        {items === null ? (
          <button type="button" className="secondary" onClick={() => onChange([emptyDraft(schema.items ?? {}, true)])}>
            Add {name}
          </button>
        ) : (
          <>
            {items.map((item, index) => (
              <div key={index} className="array-item">
                <Field
                  name={`${name} ${index + 1}`}
                  schema={schema.items ?? {}}
                  draft={item}
                  required
                  path={pathOf(path, index)}
                  errors={errors}
                  idPrefix={idPrefix}
                  onChange={(next) => onChange(items.map((existing, i) => (i === index ? next : existing)))}
                />
                <button type="button" className="secondary" onClick={() => onChange(items.filter((_, i) => i !== index))}>
                  Remove {name} {index + 1}
                </button>
              </div>
            ))}
            <button type="button" className="secondary" onClick={() => onChange([...items, emptyDraft(schema.items ?? {}, true)])}>
              Add to {name}
            </button>
            {!required && (
              <button type="button" className="secondary" onClick={() => onChange(undefined)}>
                Remove {name}
              </button>
            )}
          </>
        )}
        {error && <p className="field-error small">{error}</p>}
      </fieldset>
    )
  }

  const text = typeof draft === 'string' ? draft : ''
  const common = { id, 'aria-invalid': error ? true : undefined, 'aria-describedby': describedBy, 'aria-required': required || undefined } as const
  return (
    <div className="field">
      {label}
      {schema.enum ? (
        <select {...common} value={text} onChange={(e) => onChange(e.target.value)}>
          <option value="">{required ? 'Choose…' : '(not set)'}</option>
          {schema.enum.map((option) => (
            <option key={String(option)} value={String(option)}>
              {String(option)}
            </option>
          ))}
        </select>
      ) : (
        <span className="with-action">
          <input
            {...common}
            type="text"
            inputMode={kind === 'integer' || kind === 'number' ? 'decimal' : undefined}
            autoComplete="off"
            spellCheck={false}
            value={text}
            onChange={(e) => onChange(e.target.value)}
          />
          {schema.format === 'date-time' && (
            <button type="button" className="secondary" onClick={() => onChange(nowUtc())}>
              Now
            </button>
          )}
        </span>
      )}
      {feedback}
    </div>
  )
}

/** The inputs for one command's payload, built from its JSON Schema. */
export function SchemaForm({
  schema,
  draft,
  onChange,
  errors,
  idPrefix = 'cmd',
}: {
  schema: JsonSchema
  draft: Draft
  onChange: (draft: Draft) => void
  errors: Errors
  idPrefix?: string
}) {
  const properties = Object.entries(schema.properties ?? {})
  const need = new Set(schema.required ?? [])
  const current = draft && typeof draft === 'object' && !Array.isArray(draft) ? draft : {}
  if (properties.length === 0) return <p className="muted">This command takes no parameters.</p>
  return (
    <div className="schema-form">
      {properties.map(([name, child]) => (
        <Field
          key={name}
          name={name}
          schema={child}
          draft={current[name]}
          required={need.has(name)}
          path={name}
          errors={errors}
          idPrefix={idPrefix}
          onChange={(next) => onChange({ ...current, [name]: next })}
        />
      ))}
    </div>
  )
}
