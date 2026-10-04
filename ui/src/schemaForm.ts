/**
 * Turns the JSON Schema of an OCPP command into a form and back.
 *
 * The 19 command schemas use a small corner of JSON Schema (draft 4): objects with ``required`` fields,
 * arrays, strings (with ``enum``, ``maxLength``, ``format`` date-time or uri), integers and numbers. That is
 * all this handles; anything else is shown as a text field. It is a convenience, not the authority: the
 * broker validates commands itself in broker mode, and the charger has the last word. In particular
 * ``multipleOf`` is left to them, because checking it with floating point numbers wrongly rejects values
 * such as 0.3.
 *
 * While someone types, the form holds a *draft*: leaves are the text of their inputs, so "1." or an empty
 * box is representable. ``buildPayload`` turns a draft into the JSON to send, or says what is wrong.
 */

export interface JsonSchema {
  type?: string
  title?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  enum?: unknown[]
  format?: string
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  minItems?: number
  maxItems?: number
}

/** Text for a leaf, an object for an object, a list for an array; undefined while an optional part is left out. */
export type Draft = string | Draft[] | { [name: string]: Draft } | undefined

export type Errors = Record<string, string>

const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/
const URI = /^[A-Za-z][A-Za-z0-9+.-]*:\S+$/
const INTEGER = /^-?\d+$/

export function kindOf(schema: JsonSchema): 'object' | 'array' | 'integer' | 'number' | 'string' {
  if (schema.type === 'object' || schema.properties) return 'object'
  if (schema.type === 'array') return 'array'
  if (schema.type === 'integer') return 'integer'
  if (schema.type === 'number') return 'number'
  return 'string'
}

/** What a part of the form starts as: a required part is present and empty, an optional one is left out. */
export function emptyDraft(schema: JsonSchema, required: boolean): Draft {
  if (!required) return undefined
  switch (kindOf(schema)) {
    case 'object': {
      const draft: { [name: string]: Draft } = {}
      const need = new Set(schema.required ?? [])
      for (const [name, child] of Object.entries(schema.properties ?? {})) draft[name] = emptyDraft(child, need.has(name))
      return draft
    }
    case 'array':
      return []
    default:
      return ''
  }
}

export function pathOf(parent: string, name: string | number): string {
  return typeof name === 'number' ? `${parent}[${name}]` : parent ? `${parent}.${name}` : name
}

/** The JSON to send for a draft, and what is wrong with it, by the path of the field. ``value`` is undefined if left out. */
export function buildPayload(schema: JsonSchema, draft: Draft, required = true, path = ''): { value: unknown; errors: Errors } {
  const errors: Errors = {}
  const kind = kindOf(schema)

  if (kind === 'object') {
    if (draft === undefined) {
      if (required) errors[path] = 'Required'
      return { value: undefined, errors }
    }
    if (typeof draft !== 'object' || Array.isArray(draft)) return { value: undefined, errors: { [path]: 'Not an object' } }
    const value: Record<string, unknown> = {}
    const need = new Set(schema.required ?? [])
    for (const [name, child] of Object.entries(schema.properties ?? {})) {
      const built = buildPayload(child, draft[name], need.has(name), pathOf(path, name))
      Object.assign(errors, built.errors)
      if (built.value !== undefined) value[name] = built.value
    }
    return { value, errors }
  }

  if (kind === 'array') {
    if (draft === undefined) {
      if (required) errors[path] = 'Required'
      return { value: undefined, errors }
    }
    if (!Array.isArray(draft)) return { value: undefined, errors: { [path]: 'Not a list' } }
    const items = schema.items ?? {}
    const value: unknown[] = []
    draft.forEach((item, index) => {
      const built = buildPayload(items, item, true, pathOf(path, index))
      Object.assign(errors, built.errors)
      if (built.value !== undefined) value.push(built.value)
    })
    if (schema.minItems !== undefined && draft.length < schema.minItems) errors[path] = `At least ${schema.minItems} needed`
    if (schema.maxItems !== undefined && draft.length > schema.maxItems) errors[path] = `At most ${schema.maxItems} allowed`
    return { value, errors }
  }

  // A leaf: its draft is the text of an input
  const text = typeof draft === 'string' ? draft.trim() : ''
  if (text === '') {
    if (required) errors[path] = 'Required'
    return { value: undefined, errors }
  }
  if (kind === 'integer' || kind === 'number') {
    if (kind === 'integer' ? !INTEGER.test(text) : !Number.isFinite(Number(text))) {
      return { value: undefined, errors: { [path]: kind === 'integer' ? 'Enter a whole number' : 'Enter a number' } }
    }
    const n = Number(text)
    if (schema.minimum !== undefined && n < schema.minimum) errors[path] = `At least ${schema.minimum}`
    if (schema.maximum !== undefined && n > schema.maximum) errors[path] = `At most ${schema.maximum}`
    return { value: n, errors }
  }
  if (schema.enum && !schema.enum.includes(text)) return { value: undefined, errors: { [path]: 'Choose one of the listed values' } }
  if (schema.maxLength !== undefined && text.length > schema.maxLength) errors[path] = `At most ${schema.maxLength} characters`
  if (schema.minLength !== undefined && text.length < schema.minLength) errors[path] = `At least ${schema.minLength} characters`
  if (schema.format === 'date-time' && !DATE_TIME.test(text)) errors[path] = 'Use a date and time like 2026-10-04T12:00:00Z'
  if (schema.format === 'uri' && !URI.test(text)) errors[path] = 'Enter an address such as ftp://host/file'
  return { value: text, errors }
}

/** A draft holding ``value`` (for "use this command again" and for switching from the JSON view back to the form). */
export function draftFromValue(schema: JsonSchema, value: unknown): Draft {
  const kind = kindOf(schema)
  if (kind === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
    const draft: { [name: string]: Draft } = {}
    const given = value as Record<string, unknown>
    const need = new Set(schema.required ?? [])
    for (const [name, child] of Object.entries(schema.properties ?? {})) {
      draft[name] = name in given ? draftFromValue(child, given[name]) : emptyDraft(child, need.has(name))
    }
    return draft
  }
  if (kind === 'array') return Array.isArray(value) ? value.map((item) => draftFromValue(schema.items ?? {}, item)) : undefined
  return value === undefined || value === null ? '' : String(value)
}

/** The current time in the form OCPP uses, for a "now" button. */
export function nowUtc(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')
}
