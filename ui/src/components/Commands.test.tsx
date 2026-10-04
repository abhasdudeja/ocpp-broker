import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { brokerEvent, catalog, detail, mockApi, mockEvents, renderApp, respond, signedIn } from '../test-utils'

const PATH = '/api/chargers/Fleet/CP-001'
const SEND = '/api/ocpp/organizations/Fleet/chargers/CP-001/commands'

const record = (status: string, extra: Record<string, unknown> = {}) => ({
  message_id: 'm-1',
  charger_id: 'CP-001',
  action: 'x',
  status,
  response: null,
  error: null,
  timestamp: new Date().toISOString(),
  ...extra,
})

const entry = (overrides: Record<string, unknown> = {}) => ({
  message_id: 'abc-123',
  action: 'ChangeAvailability',
  status: 'success',
  payload: { connectorId: 1, type: 'Inoperative' },
  response: { status: 'Accepted' },
  error: null,
  sent_at: new Date(Date.now() - 60_000).toISOString(),
  finished_at: new Date(Date.now() - 59_000).toISOString(),
  duration_ms: 120,
  ...overrides,
})

/** The charger page with a broker that answers commands with whatever ``reply`` returns. */
async function setup(options: { reply?: () => Response; routes?: Record<string, unknown>; events?: ReturnType<typeof mockEvents> } = {}) {
  let reply = options.reply ?? (() => respond(record('success', { response: { status: 'Accepted' } })))
  const fetch = mockApi({ [PATH]: detail(), [SEND]: () => reply(), ...options.routes }, options.events?.handler)
  signedIn()
  renderApp('/chargers/Fleet/CP-001')
  await screen.findByRole('form', { name: 'Send a command' })
  await screen.findByLabelText('Command')
  const posts = () =>
    fetch.mock.calls
      .filter(([, init]) => init?.method === 'POST')
      .map(([url, init]) => ({ url: String(url), body: JSON.parse(String(init?.body)) as Record<string, unknown> }))
  const gets = (suffix: string) => fetch.mock.calls.filter(([url, init]) => String(url).endsWith(suffix) && init?.method !== 'POST').length
  return { fetch, posts, gets, setReply: (next: () => Response) => (reply = next), user: userEvent.setup() }
}

const choose = async (user: ReturnType<typeof userEvent.setup>, action: string) => {
  await screen.findByRole('option', { name: action })
  await user.selectOptions(screen.getByLabelText('Command'), action)
}
const field = (name: string) => screen.getByLabelText(new RegExp(`^${name}`))
const outcome = () => screen.getByRole('status', { name: 'Result of the command' })
const findOutcome = () => screen.findByRole('status', { name: 'Result of the command' })
const sendButton = (action: string) => screen.getByRole('button', { name: `Send ${action}` })

describe('the command panel', () => {
  it('offers the commands in groups by how much they can do', async () => {
    await setup()
    const select = screen.getByLabelText('Command')
    const groups = within(select)
      .getAllByRole('group')
      .map((g) => [g.getAttribute('label'), within(g).getAllByRole('option').length])
    expect(groups).toEqual([
      ['Read-only', 4],
      ['Changes settings', 9],
      ['Can interrupt the charger', 6],
    ])
    expect(within(select).getByRole('option', { name: 'Reset' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Send / })).not.toBeInTheDocument()
  })

  it('shows the fields of the chosen command, marking the required ones', async () => {
    const { user } = await setup()
    await choose(user, 'ChangeAvailability')
    expect(screen.getByText(/Make a connector or the charger operative or inoperative/)).toBeInTheDocument()
    expect(screen.getByText('Can interrupt the charger', { selector: '.chip' })).toBeInTheDocument()
    expect(field('connectorId')).toHaveAttribute('aria-required', 'true')
    expect(within(field('type') as HTMLElement).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose…', 'Inoperative', 'Operative'])
  })

  it('says when a command takes no parameters', async () => {
    const { user } = await setup()
    await choose(user, 'ClearCache')
    expect(screen.getByText('This command takes no parameters.')).toBeInTheDocument()
  })

  it('names what is wrong and sends nothing until it is fixed', async () => {
    const { user, posts } = await setup()
    await choose(user, 'ReserveNow')
    await user.type(field('connectorId'), 'one')
    await user.type(field('expiryDate'), 'tomorrow')
    await user.click(sendButton('ReserveNow'))
    expect(await screen.findByText('Some fields need attention.')).toBeInTheDocument()
    expect(field('connectorId')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Enter a whole number')).toBeInTheDocument()
    expect(screen.getByText('Use a date and time like 2026-10-04T12:00:00Z', { selector: '.field-error' })).toBeInTheDocument()
    expect(screen.getAllByText('Required')).toHaveLength(2) // idTag, reservationId
    expect(field('connectorId')).toHaveAccessibleDescription('Enter a whole number')
    expect(posts()).toEqual([])
  })

  it('sends the typed payload with the wait time and shows what the charger answered', async () => {
    const { user, posts } = await setup()
    await choose(user, 'ReserveNow')
    await user.type(field('connectorId'), '2')
    await user.type(field('expiryDate'), '2026-10-04T12:00:00Z')
    await user.type(field('idTag'), 'TAG1')
    await user.type(field('reservationId'), '9')
    await user.click(sendButton('ReserveNow'))
    expect(await screen.findByText('ReserveNow: the charger answered')).toBeInTheDocument()
    expect(outcome()).toHaveTextContent('"status": "Accepted"')
    expect(posts()).toEqual([
      { url: SEND, body: { action: 'ReserveNow', payload: { connectorId: 2, expiryDate: '2026-10-04T12:00:00Z', idTag: 'TAG1', reservationId: 9 }, timeout: 30 } },
    ])
  })

  it('reads the history again as soon as a command has finished, even with no event stream', async () => {
    const { user, gets } = await setup()
    await screen.findByText('No commands have been sent to this charger since it connected.')
    const before = gets('/commands')
    await choose(user, 'ClearCache')
    await user.click(sendButton('ClearCache'))
    await screen.findByText('ClearCache: the charger answered')
    await vi.waitFor(() => expect(gets('/commands')).toBe(before + 1))
  })

  it('fills in the current time on request', async () => {
    const { user } = await setup()
    await choose(user, 'ReserveNow')
    await user.click(screen.getByRole('button', { name: 'Now' }))
    expect((field('expiryDate') as HTMLInputElement).value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/)
  })

  it('sends a read-only command straight away, with a list built up item by item', async () => {
    const { user, posts } = await setup()
    await choose(user, 'GetConfiguration')
    await user.click(screen.getByRole('button', { name: 'Add key' }))
    await user.type(screen.getByLabelText(/^key 1/), 'HeartbeatInterval')
    await user.click(screen.getByRole('button', { name: 'Add to key' }))
    await user.type(screen.getByLabelText(/^key 2/), 'MeterValueSampleInterval')
    await user.click(screen.getByRole('button', { name: 'Remove key 1' }))
    expect(screen.getByLabelText(/^key 1/)).toHaveValue('MeterValueSampleInterval')
    await user.click(sendButton('GetConfiguration'))
    await screen.findByText('GetConfiguration: the charger answered')
    expect(posts()[0]?.body).toEqual({ action: 'GetConfiguration', payload: { key: ['MeterValueSampleInterval'] }, timeout: 30 })
  })

  it('adds and removes an optional nested object', async () => {
    const { user, posts } = await setup()
    await choose(user, 'SendLocalList')
    await user.type(field('listVersion'), '4')
    await user.selectOptions(field('updateType'), 'Full')
    await user.click(screen.getByRole('button', { name: 'Add localAuthorizationList' }))
    await user.type(screen.getByLabelText(/^idTag/), 'CARD1')
    await user.click(screen.getByRole('button', { name: 'Add idTagInfo' }))
    await user.selectOptions(screen.getByLabelText(/^status/), 'Blocked')
    await user.click(sendButton('SendLocalList'))
    await screen.findByText('SendLocalList: the charger answered')
    expect(posts()[0]?.body.payload).toEqual({
      listVersion: 4,
      updateType: 'Full',
      localAuthorizationList: [{ idTag: 'CARD1', idTagInfo: { status: 'Blocked' } }],
    })
    await user.click(screen.getByRole('button', { name: 'Remove idTagInfo' }))
    expect(screen.queryByLabelText(/^status/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add idTagInfo' })).toBeInTheDocument()
  })

  describe('commands that can interrupt the charger', () => {
    it('ask first, and send nothing if the answer is no', async () => {
      const { user, posts } = await setup()
      await choose(user, 'Reset')
      await user.selectOptions(field('type'), 'Hard')
      await user.click(sendButton('Reset'))
      const dialog = await screen.findByRole('alertdialog', { name: 'Confirm Reset' })
      expect(dialog).toHaveTextContent('Send Reset to CP-001?')
      expect(posts()).toEqual([])
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
      expect(posts()).toEqual([])
      expect(sendButton('Reset')).toBeInTheDocument()
    })

    it('send it once confirmed', async () => {
      const { user, posts } = await setup()
      await choose(user, 'Reset')
      await user.selectOptions(field('type'), 'Soft')
      await user.click(sendButton('Reset'))
      await user.click(await screen.findByRole('button', { name: 'Yes, send Reset' }))
      await screen.findByText('Reset: the charger answered')
      expect(posts()).toHaveLength(1)
      expect(posts()[0]?.body).toEqual({ action: 'Reset', payload: { type: 'Soft' }, timeout: 30 })
    })

    it('are not confirmed when the form is wrong: the mistake is shown instead', async () => {
      const { user } = await setup()
      await choose(user, 'Reset')
      await user.click(sendButton('Reset'))
      expect(await screen.findByText('Some fields need attention.')).toBeInTheDocument()
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    })
  })

  describe('how a command ended', () => {
    const sendReset = async (user: ReturnType<typeof userEvent.setup>) => {
      await choose(user, 'ClearCache')
      await user.click(sendButton('ClearCache'))
    }

    it('says so when the charger does not answer in time, using the wait the operator chose', async () => {
      const { user, posts } = await setup({ reply: () => respond(record('timeout', { error: 'No response within 5s' }), 504) })
      await choose(user, 'ClearCache')
      await user.clear(field('Wait for the charger up to'))
      await user.type(field('Wait for the charger up to'), '5')
      await user.click(sendButton('ClearCache'))
      expect(await screen.findByText('ClearCache: no answer within 5 s')).toBeInTheDocument()
      expect(posts()[0]?.body.timeout).toBe(5)
    })

    it('says so when the charger refuses, with its reason', async () => {
      const { user } = await setup({ reply: () => respond(record('error', { error: 'NotSupported: no cache' })) })
      await sendReset(user)
      expect(await screen.findByText('ClearCache: the charger refused it')).toBeInTheDocument()
      expect(outcome()).toHaveTextContent('NotSupported: no cache')
      expect(outcome().className).toContain('outcome-bad')
    })

    it.each([
      [404, 'This charger is no longer connected to this broker instance, so nothing was sent.'],
      [422, 'The broker rejected the command and sent nothing: Invalid payload for Reset'],
      [503, 'The charger could not be reached: Charger CP-001 is disconnected'],
      [500, 'The broker answered with an error: kaboom'],
    ])('explains an HTTP %i answer', async (status, message) => {
      const detailText = { 404: 'gone', 422: 'Invalid payload for Reset', 503: 'Charger CP-001 is disconnected', 500: 'kaboom' }[status]
      const { user } = await setup({ reply: () => respond({ detail: detailText }, status) })
      await sendReset(user)
      expect(await findOutcome()).toHaveTextContent(message)
    })

    it('says when the broker cannot be reached that the command may or may not have gone out', async () => {
      const { user, setReply } = await setup()
      setReply(() => {
        throw new TypeError('network down')
      })
      await sendReset(user)
      expect(await findOutcome()).toHaveTextContent('Could not reach the broker. The command may or may not have been sent')
    })

    it('signs out if the broker stopped accepting the key', async () => {
      const { user } = await setup({ reply: () => respond({ detail: 'no' }, 401) })
      await sendReset(user)
      expect(await screen.findByLabelText('API key')).toBeInTheDocument()
    })

    it('cannot be sent twice while waiting', async () => {
      let release: (response: Response) => void = () => {}
      const pending = new Promise<Response>((resolve) => {
        release = resolve
      })
      const { user, posts, fetch } = await setup()
      fetch.mockImplementation((url, init) => (init?.method === 'POST' ? pending : Promise.resolve(respond({ commands: [], ...(String(url).includes('catalog') ? catalog() : {}) }))))
      await choose(user, 'ClearCache')
      await user.click(sendButton('ClearCache'))
      const waiting = await screen.findByRole('button', { name: 'Waiting for the charger…' })
      expect(waiting).toBeDisabled()
      expect(screen.getByLabelText('Command')).toBeDisabled()
      expect(posts()).toHaveLength(1)
      await act(async () => release(respond(record('success'))))
      expect(await screen.findByText('ClearCache: the charger answered')).toBeInTheDocument()
      expect(sendButton('ClearCache')).toBeEnabled()
    })
  })

  describe('the wait time', () => {
    it.each(['0', '301', 'abc', '', '1.5', '-5'])('rejects %j and sends nothing', async (bad) => {
      const { user, posts } = await setup()
      await choose(user, 'ClearCache')
      await user.clear(field('Wait for the charger up to'))
      if (bad) await user.type(field('Wait for the charger up to'), bad)
      await user.click(sendButton('ClearCache'))
      expect(await screen.findByText('The time to wait for the charger is 1 to 300 seconds.')).toBeInTheDocument()
      expect(posts()).toEqual([])
    })

    it('accepts the limits', async () => {
      const { user, posts } = await setup()
      await choose(user, 'ClearCache')
      for (const good of ['1', '300']) {
        await user.clear(field('Wait for the charger up to'))
        await user.type(field('Wait for the charger up to'), good)
        await user.click(sendButton('ClearCache'))
        await vi.waitFor(() => expect(posts().length).toBeGreaterThan(0))
      }
      expect(posts().map((p) => p.body.timeout)).toEqual([1, 300])
    })
  })

  describe('as JSON', () => {
    it('shows what the form holds, and sends what is written, checked only for being a JSON object', async () => {
      const { user, posts } = await setup()
      await choose(user, 'ChangeConfiguration')
      await user.type(field('key'), 'HeartbeatInterval')
      await user.type(field('value'), '60')
      await user.click(screen.getByRole('button', { name: 'JSON' }))
      const box = screen.getByLabelText('Payload (JSON)') as HTMLTextAreaElement
      expect(JSON.parse(box.value)).toEqual({ key: 'HeartbeatInterval', value: '60' })
      expect(screen.getByText(/not checked against the command's schema/)).toBeInTheDocument()

      await user.clear(box)
      await user.click(box)
      await user.paste('{"key": "HeartbeatInterval", "value": 60, "extra": {"anything": true}}')
      await user.click(sendButton('ChangeConfiguration'))
      await vi.waitFor(() => expect(posts()).toHaveLength(1))
      expect(posts()[0]?.body.payload).toEqual({ key: 'HeartbeatInterval', value: 60, extra: { anything: true } })
    })

    it.each([
      ['{broken', 'The JSON is not valid.'],
      ['[1, 2]', 'A command payload is a JSON object, like {"connectorId": 1}.'],
      ['"text"', 'A command payload is a JSON object, like {"connectorId": 1}.'],
    ])('refuses %s', async (text, message) => {
      const { user, posts } = await setup()
      await choose(user, 'ClearCache')
      await user.click(screen.getByRole('button', { name: 'JSON' }))
      const box = screen.getByLabelText('Payload (JSON)')
      await user.clear(box)
      await user.click(box)
      await user.paste(text)
      await user.click(sendButton('ClearCache'))
      expect(await screen.findByText(message)).toBeInTheDocument()
      expect(posts()).toEqual([])
    })

    it('goes back to the form with the values that were written, and will not if the JSON is broken', async () => {
      const { user } = await setup()
      await choose(user, 'ChangeAvailability')
      await user.click(screen.getByRole('button', { name: 'JSON' }))
      const box = screen.getByLabelText('Payload (JSON)')
      await user.clear(box)
      await user.click(box)
      await user.paste('{oops')
      await user.click(screen.getByRole('button', { name: 'Form' }))
      expect(await screen.findByText(/cannot be shown as a form/)).toBeInTheDocument()
      expect(screen.getByLabelText('Payload (JSON)')).toBeInTheDocument()

      await user.clear(box)
      await user.click(box)
      await user.paste('{"connectorId": 3, "type": "Inoperative"}')
      await user.click(screen.getByRole('button', { name: 'Form' }))
      expect(field('connectorId')).toHaveValue('3')
      expect(field('type')).toHaveValue('Inoperative')
      expect(screen.queryByText(/cannot be shown as a form/)).not.toBeInTheDocument()
    })

    it('will not show an array as a form either', async () => {
      const { user } = await setup()
      await choose(user, 'ChangeAvailability')
      await user.click(screen.getByRole('button', { name: 'JSON' }))
      const box = screen.getByLabelText('Payload (JSON)')
      await user.clear(box)
      await user.click(box)
      await user.paste('[1, 2]')
      await user.click(screen.getByRole('button', { name: 'Form' }))
      expect(await screen.findByText('A command payload is a JSON object, like {"connectorId": 1}.')).toBeInTheDocument()
      expect(screen.getByLabelText('Payload (JSON)')).toBeInTheDocument()
    })

    it('marks which view is in use', async () => {
      const { user } = await setup()
      await choose(user, 'ClearCache')
      expect(screen.getByRole('button', { name: 'Form' })).toHaveAttribute('aria-pressed', 'true')
      await user.click(screen.getByRole('button', { name: 'JSON' }))
      expect(screen.getByRole('button', { name: 'JSON' })).toHaveAttribute('aria-pressed', 'true')
      expect(screen.getByRole('button', { name: 'Form' })).toHaveAttribute('aria-pressed', 'false')
    })
  })

  it('shows a form for every command in the real catalog', async () => {
    const { user } = await setup()
    for (const command of catalog().commands) {
      await choose(user, command.action)
      expect(await screen.findByRole('button', { name: `Send ${command.action}` }), command.action).toBeInTheDocument()
      const required = ((command.json_schema as { required?: string[] }).required ?? []) as string[]
      for (const name of required) expect(screen.getAllByText(new RegExp(`^${name}`)).length, `${command.action}.${name}`).toBeGreaterThan(0)
    }
  }, 30_000)

  it('reports a command list it could not load', async () => {
    await setup({ routes: { '/api/ocpp/commands/catalog': respond({ detail: 'catalog on fire' }, 500) } }).catch(() => undefined)
    expect(await screen.findByText(/Could not load the command list: catalog on fire/)).toBeInTheDocument()
  })
})

describe('the history of commands', () => {
  it('lists what was sent, newest first as the broker gives it, with its outcome and how long it took', async () => {
    await setup({
      routes: {
        [`${PATH}/commands`]: {
          commands: [
            entry({ message_id: 'b', action: 'Reset', status: 'timeout', response: null, error: 'No response within 30s', duration_ms: 30000, payload: { type: 'Soft' } }),
            entry({ message_id: 'a' }),
          ],
        },
      },
    })
    const history = within(await screen.findByRole('list', { name: '' }).catch(() => screen.getAllByRole('list').find((l) => l.className === 'history') as HTMLElement))
    const items = history.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('Reset')
    expect(items[0]).toHaveTextContent('timeout')
    expect(items[0]).toHaveTextContent('took 30000 ms')
    expect(items[0]).toHaveTextContent('No response within 30s')
    expect(items[1]).toHaveTextContent('ChangeAvailability')
    expect(items[1]).toHaveTextContent('success')
    expect(items[1]).toHaveTextContent('1m ago')
  })

  it('shows what was sent and answered when asked', async () => {
    const { user } = await setup({ routes: { [`${PATH}/commands`]: { commands: [entry()] } } })
    await screen.findByText('Sent to this charger')
    await user.click(await screen.findByText('Details'))
    expect(screen.getByText(/"type": "Inoperative"/)).toBeInTheDocument()
    expect(screen.getByText(/"status": "Accepted"/)).toBeInTheDocument()
    expect(screen.getByText('abc-123')).toBeInTheDocument()
  })

  it('puts a past command back in the panel as JSON, ready to send again', async () => {
    const { user, posts } = await setup({
      routes: { [`${PATH}/commands`]: { commands: [entry({ action: 'ChangeConfiguration', payload: { key: 'HeartbeatInterval', value: '60' } })] } },
    })
    await user.click(await screen.findByText('Details'))
    await user.click(screen.getByRole('button', { name: 'Use again' }))
    expect(await screen.findByLabelText('Command')).toHaveValue('ChangeConfiguration')
    expect(JSON.parse((screen.getByLabelText('Payload (JSON)') as HTMLTextAreaElement).value)).toEqual({ key: 'HeartbeatInterval', value: '60' })
    await user.click(sendButton('ChangeConfiguration'))
    await vi.waitFor(() => expect(posts()).toHaveLength(1))
    expect(posts()[0]?.body.payload).toEqual({ key: 'HeartbeatInterval', value: '60' })
  })

  it('says when nothing has been sent', async () => {
    await setup()
    expect(await screen.findByText('No commands have been sent to this charger since it connected.')).toBeInTheDocument()
  })

  it('says why it is empty when the history cannot be read', async () => {
    await setup({ routes: { [`${PATH}/commands`]: respond({ detail: 'gone' }, 404) } })
    expect(await screen.findByText('Not available while the charger is not connected.')).toBeInTheDocument()
  })

  it('refreshes when a command to this charger finishes, and not for another charger', async () => {
    const events = mockEvents()
    let calls = 0
    const { gets } = await setup({
      events,
      routes: {
        [`${PATH}/commands`]: () => {
          calls += 1
          return { commands: calls > 1 ? [entry()] : [] }
        },
      },
    })
    await vi.waitFor(() => expect(events.streams.length).toBeGreaterThan(0))
    act(() => events.latest()?.open())
    await screen.findByText('No commands have been sent to this charger since it connected.')
    const before = gets('/commands')

    act(() => events.latest()?.emit(brokerEvent('command.result', { action: 'Reset', status: 'success' }, { charger_id: 'CP-002' })))
    await act(() => new Promise((resolve) => setTimeout(resolve, 250)))
    expect(gets('/commands')).toBe(before)

    act(() => events.latest()?.emit(brokerEvent('command.result', { action: 'Reset', status: 'success' })))
    expect(await screen.findByText('Details')).toBeInTheDocument()
    expect(gets('/commands')).toBe(before + 1)
  })

  it('is withdrawn when the charger disconnects while the page is open', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let present = true
    mockApi({ [PATH]: () => (present ? detail() : respond({ detail: 'gone' }, 404)) })
    signedIn()
    renderApp('/chargers/Fleet/CP-001')
    await screen.findByRole('form', { name: 'Send a command' })
    present = false
    await act(() => vi.advanceTimersByTimeAsync(3_000))
    expect(await screen.findByRole('alert')).toHaveTextContent('not connected to this broker instance any more')
    expect(screen.queryByRole('form', { name: 'Send a command' })).not.toBeInTheDocument()
    expect(screen.getByText('SN-7'), 'what was known stays on screen').toBeInTheDocument()
  })

  it('is not offered for a charger that has gone', async () => {
    mockApi({ [PATH]: respond({ detail: 'gone' }, 404) })
    signedIn()
    renderApp('/chargers/Fleet/CP-001')
    expect(await screen.findByRole('alert')).toHaveTextContent('not connected to this broker instance')
    expect(screen.queryByRole('form', { name: 'Send a command' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Commands' })).not.toBeInTheDocument()
  })
})
