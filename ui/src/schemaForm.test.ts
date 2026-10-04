import { describe, expect, it } from 'vitest'

import { catalog } from './test-utils'
import { buildPayload, draftFromValue, emptyDraft, kindOf, nowUtc, pathOf, type Draft, type JsonSchema } from './schemaForm'

const spec = (action: string): JsonSchema => {
  const found = catalog().commands.find((c) => c.action === action)
  if (!found) throw new Error(`no ${action} in the catalog`)
  return found.json_schema as JsonSchema
}

describe('emptyDraft', () => {
  it('leaves optional parts out and makes required parts present and empty', () => {
    const schema = spec('ReserveNow')
    expect(emptyDraft(schema, true)).toEqual({ connectorId: '', expiryDate: '', idTag: '', parentIdTag: undefined, reservationId: '' })
    expect(emptyDraft(schema, false)).toBeUndefined()
  })

  it('creates nested required objects and empty required arrays', () => {
    const draft = emptyDraft(spec('SetChargingProfile'), true) as Record<string, Record<string, Draft>>
    expect(draft.csChargingProfiles).toBeDefined()
    expect(draft.csChargingProfiles?.chargingSchedule).toBeDefined()
    expect((draft.csChargingProfiles?.chargingSchedule as Record<string, Draft>).chargingSchedulePeriod).toEqual([])
    expect(draft.connectorId).toBe('')
  })
})

describe('buildPayload', () => {
  const reserve = spec('ReserveNow')
  const filled = { connectorId: '1', expiryDate: '2026-10-04T12:00:00Z', idTag: 'TAG1', parentIdTag: '', reservationId: '7' }

  it('turns the text of the inputs into typed JSON and leaves out what is empty', () => {
    // strictly: a left-out field is absent, not present with the value undefined
    expect(buildPayload(reserve, filled)).toStrictEqual({
      value: { connectorId: 1, expiryDate: '2026-10-04T12:00:00Z', idTag: 'TAG1', reservationId: 7 },
      errors: {},
    })
  })

  it('names every missing required field', () => {
    const { errors } = buildPayload(reserve, emptyDraft(reserve, true))
    expect(errors).toEqual({ connectorId: 'Required', expiryDate: 'Required', idTag: 'Required', reservationId: 'Required' })
  })

  it('trims what was typed, and treats blank as empty', () => {
    expect(buildPayload(reserve, { ...filled, idTag: '  TAG1  ' }).value).toMatchObject({ idTag: 'TAG1' })
    expect(buildPayload(reserve, { ...filled, idTag: '   ' }).errors).toEqual({ idTag: 'Required' })
  })

  it('accepts only whole numbers for an integer', () => {
    for (const bad of ['1.5', 'one', '1e3', '0x10', '--1', '1 2']) {
      expect(buildPayload(reserve, { ...filled, connectorId: bad }).errors, bad).toEqual({ connectorId: 'Enter a whole number' })
    }
    expect(buildPayload(reserve, { ...filled, connectorId: '-3' }).value).toMatchObject({ connectorId: -3 })
  })

  it('accepts decimals for a number', () => {
    const schema: JsonSchema = { type: 'object', properties: { limit: { type: 'number' } }, required: ['limit'] }
    expect(buildPayload(schema, { limit: '0.3' }).value).toEqual({ limit: 0.3 })
    expect(buildPayload(schema, { limit: '.5' }).value).toEqual({ limit: 0.5 })
    expect(buildPayload(schema, { limit: 'abc' }).errors).toEqual({ limit: 'Enter a number' })
    expect(buildPayload(schema, { limit: 'Infinity' }).errors).toEqual({ limit: 'Enter a number' })
  })

  it('does not reject 0.3 for multipleOf 0.1: that check is left to the broker and the charger', () => {
    const schema = spec('SetChargingProfile') as JsonSchema
    const limit = (schema.properties?.csChargingProfiles?.properties?.chargingSchedule?.properties?.chargingSchedulePeriod?.items?.properties?.limit ?? {}) as JsonSchema & { multipleOf?: number }
    expect(limit.multipleOf).toBe(0.1)
    expect(buildPayload({ type: 'object', properties: { limit }, required: ['limit'] }, { limit: '0.3' }).errors).toEqual({})
  })

  it('checks enums, lengths, bounds and formats', () => {
    const schema: JsonSchema = {
      type: 'object',
      properties: {
        kind: { type: 'string', enum: ['Hard', 'Soft'] },
        tag: { type: 'string', maxLength: 3, minLength: 2 },
        n: { type: 'integer', minimum: 1, maximum: 5 },
        when: { type: 'string', format: 'date-time' },
        where: { type: 'string', format: 'uri' },
      },
    }
    const bad = buildPayload(schema, { kind: 'Medium', tag: 'abcd', n: '9', when: 'yesterday', where: 'not a uri' })
    expect(bad.errors).toEqual({
      kind: 'Choose one of the listed values',
      tag: 'At most 3 characters',
      n: 'At most 5',
      when: 'Use a date and time like 2026-10-04T12:00:00Z',
      where: 'Enter an address such as ftp://host/file',
    })
    expect(buildPayload(schema, { tag: 'a', n: '0' }).errors).toEqual({ tag: 'At least 2 characters', n: 'At least 1' })
    const good = buildPayload(schema, { kind: 'Soft', tag: 'abc', n: '5', when: '2026-10-04T12:00:00.5+02:00', where: 'ftp://host/file.bin' })
    expect(good.errors).toEqual({})
    expect(good.value).toEqual({ kind: 'Soft', tag: 'abc', n: 5, when: '2026-10-04T12:00:00.5+02:00', where: 'ftp://host/file.bin' })
  })

  it('accepts exactly the date-time forms OCPP uses', () => {
    const schema: JsonSchema = { type: 'object', properties: { when: { type: 'string', format: 'date-time' } } }
    for (const ok of ['2026-10-04T12:00:00Z', '2026-10-04T12:00:00.123Z', '2026-10-04T12:00:00+05:30']) {
      expect(buildPayload(schema, { when: ok }).errors, ok).toEqual({})
    }
    for (const bad of ['2026-10-04', '2026-10-04 12:00:00', '2026-10-04T12:00Z', '12:00:00Z', '2026-10-04T12:00:00']) {
      expect(buildPayload(schema, { when: bad }).errors, bad).toEqual({ when: 'Use a date and time like 2026-10-04T12:00:00Z' })
    }
  })

  it('builds nested objects, and reports errors at the path of the field', () => {
    const list = spec('SendLocalList')
    const draft = {
      listVersion: '3',
      updateType: 'Full',
      localAuthorizationList: [{ idTag: 'A', idTagInfo: { status: 'Accepted', expiryDate: '', parentIdTag: '' } }, { idTag: 'B', idTagInfo: undefined }],
    }
    expect(buildPayload(list, draft)).toEqual({
      value: {
        listVersion: 3,
        updateType: 'Full',
        localAuthorizationList: [{ idTag: 'A', idTagInfo: { status: 'Accepted' } }, { idTag: 'B' }],
      },
      errors: {},
    })
    const broken = buildPayload(list, {
      ...draft,
      localAuthorizationList: [{ idTag: '', idTagInfo: { status: '', expiryDate: 'x', parentIdTag: '' } }],
    })
    expect(broken.errors).toEqual({
      'localAuthorizationList[0].idTag': 'Required',
      'localAuthorizationList[0].idTagInfo.status': 'Required',
      'localAuthorizationList[0].idTagInfo.expiryDate': 'Use a date and time like 2026-10-04T12:00:00Z',
    })
  })

  it('leaves an optional array out, and sends an empty one when it was added and left empty', () => {
    const config = spec('GetConfiguration')
    expect(buildPayload(config, { key: undefined })).toEqual({ value: {}, errors: {} })
    expect(buildPayload(config, { key: [] })).toEqual({ value: { key: [] }, errors: {} })
    expect(buildPayload(config, { key: ['A', 'B'] }).value).toEqual({ key: ['A', 'B'] })
    expect(buildPayload(config, { key: ['A', ''] }).errors).toEqual({ 'key[1]': 'Required' })
  })

  it('enforces the number of items when the schema says so', () => {
    const schema: JsonSchema = { type: 'object', properties: { xs: { type: 'array', items: { type: 'string' }, minItems: 1, maxItems: 2 } } }
    expect(buildPayload(schema, { xs: [] }).errors).toEqual({ xs: 'At least 1 needed' })
    expect(buildPayload(schema, { xs: ['a', 'b', 'c'] }).errors).toEqual({ xs: 'At most 2 allowed' })
  })

  it('reports a draft of the wrong shape instead of crashing', () => {
    expect(buildPayload(reserve, 'text').errors).toEqual({ '': 'Not an object' })
    expect(buildPayload(spec('GetConfiguration'), { key: 'text' }).errors).toEqual({ key: 'Not a list' })
  })

  it('treats a schema without a type as text', () => {
    expect(kindOf({})).toBe('string')
    expect(buildPayload({ type: 'object', properties: { x: {} } }, { x: 'hello' }).value).toEqual({ x: 'hello' })
  })
})

describe('draftFromValue', () => {
  it('gives back the same payload it was made from', () => {
    const payload = { connectorId: 1, expiryDate: '2026-10-04T12:00:00Z', idTag: 'TAG1', parentIdTag: 'P', reservationId: 7 }
    const schema = spec('ReserveNow')
    expect(buildPayload(schema, draftFromValue(schema, payload)).value).toEqual(payload)
  })

  it('round-trips nested objects and arrays', () => {
    const schema = spec('SendLocalList')
    const payload = {
      listVersion: 3,
      updateType: 'Differential',
      localAuthorizationList: [{ idTag: 'A', idTagInfo: { status: 'Blocked', expiryDate: '2026-10-04T12:00:00Z' } }, { idTag: 'B' }],
    }
    expect(buildPayload(schema, draftFromValue(schema, payload)).value).toEqual(payload)
  })

  it('drops keys the schema does not know and fills required fields that are missing with empty ones', () => {
    const schema = spec('ReserveNow')
    const draft = draftFromValue(schema, { idTag: 'T', stray: 1 }) as Record<string, Draft>
    expect(draft.stray).toBeUndefined()
    expect(draft.connectorId).toBe('')
    expect(draft.parentIdTag).toBeUndefined()
  })

  it('shows a value of the wrong type as text so it can be corrected', () => {
    const schema = spec('ReserveNow')
    expect(buildPayload(schema, draftFromValue(schema, { connectorId: 'one', expiryDate: '2026-10-04T12:00:00Z', idTag: 'T', reservationId: 1 })).errors).toEqual({
      connectorId: 'Enter a whole number',
    })
  })

  it('copes with values that are not objects or arrays', () => {
    expect(draftFromValue(spec('ReserveNow'), 5)).toBeUndefined()
    expect(draftFromValue(spec('GetConfiguration'), { key: 'x' })).toEqual({ key: undefined })
  })
})

describe('the real command schemas', () => {
  const commands = catalog().commands

  it.each(commands.map((c) => [c.action]))('%s: an empty form names exactly the required fields', (action) => {
    const schema = spec(action)
    const { errors, value } = buildPayload(schema, emptyDraft(schema, true))
    const required = new Set(schema.required ?? [])
    for (const path of Object.keys(errors)) expect(errors[path]).toBe('Required')
    // top-level required leaves are reported; nested ones appear when their parent exists
    for (const name of required) {
      const kind = kindOf(schema.properties?.[name] ?? {})
      if (kind !== 'array') expect(Object.keys(errors).some((p) => p === name || p.startsWith(`${name}.`)), name).toBe(true)
    }
    expect(value).toBeTypeOf('object')
  })

  it('needs no input for the commands that have no required fields', () => {
    for (const action of ['ClearCache', 'GetConfiguration', 'GetLocalListVersion', 'ClearChargingProfile']) {
      const schema = spec(action)
      expect(buildPayload(schema, emptyDraft(schema, true)).errors, action).toEqual({})
    }
  })
})

describe('small helpers', () => {
  it('builds paths for nested errors', () => {
    expect(pathOf('', 'a')).toBe('a')
    expect(pathOf('a', 'b')).toBe('a.b')
    expect(pathOf('a', 2)).toBe('a[2]')
  })

  it('gives the time the way OCPP writes it', () => {
    expect(nowUtc()).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/)
  })
})
