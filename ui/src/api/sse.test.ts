import { describe, expect, it } from 'vitest'

import { readSse, type SseItem } from './sse'

const encoder = new TextEncoder()

/** A body that delivers exactly these chunks (strings are UTF-8 encoded; byte arrays are sent as they are). */
function body(...chunks: Array<string | Uint8Array>): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk)
      controller.close()
    },
  })
}

async function read(...chunks: Array<string | Uint8Array>): Promise<SseItem[]> {
  const items: SseItem[] = []
  for await (const item of readSse(body(...chunks))) items.push(item)
  return items
}

const message = (event: string, data: string, id: string | null = null): SseItem => ({ kind: 'message', id, event, data })

describe('readSse', () => {
  it('reads a message with its id, event name and data', async () => {
    expect(await read('id: 7\nevent: charger.status\ndata: {"a":1}\n\n')).toEqual([message('charger.status', '{"a":1}', '7')])
  })

  it('reads several messages in one chunk, in order', async () => {
    expect(await read('event: a\ndata: 1\n\nevent: b\ndata: 2\n\n')).toEqual([message('a', '1'), message('b', '2')])
  })

  it('calls a message without an event name "message", as the standard says', async () => {
    expect(await read('data: hello\n\n')).toEqual([message('message', 'hello')])
  })

  it('joins several data lines with a newline', async () => {
    expect(await read('data: one\ndata: two\ndata:\ndata: four\n\n')).toEqual([message('message', 'one\ntwo\n\nfour')])
  })

  it('removes one leading space from a value and no more', async () => {
    expect(await read('data:  two spaces\n\ndata:none\n\n')).toEqual([message('message', ' two spaces'), message('message', 'none')])
  })

  it('reports comments (the broker keepalive) separately and does not mistake them for messages', async () => {
    expect(await read(': keepalive\n\n')).toEqual([{ kind: 'comment', text: 'keepalive' }])
  })

  it('does not report a message that has no data', async () => {
    expect(await read('event: lonely\n\nid: 3\n\n')).toEqual([])
  })

  it('does not carry the event name or id of one message into the next', async () => {
    expect(await read('id: 1\nevent: a\ndata: x\n\ndata: y\n\n')).toEqual([message('a', 'x', '1'), message('message', 'y')])
  })

  it('accepts CRLF and CR line endings', async () => {
    expect(await read('event: a\r\ndata: 1\r\n\r\n')).toEqual([message('a', '1')])
    expect(await read('event: b\rdata: 2\r\r')).toEqual([message('b', '2')])
  })

  it('copes with a chunk ending anywhere: mid-field, between a CR and its LF, between messages', async () => {
    const whole = 'id: 5\r\nevent: x\r\ndata: {"k":"v"}\r\n\r\nid: 6\nevent: y\ndata: 2\n\n'
    const expected = [message('x', '{"k":"v"}', '5'), message('y', '2', '6')]
    for (let cut = 1; cut < whole.length; cut += 1) {
      expect(await read(whole.slice(0, cut), whole.slice(cut)), `split at ${cut}`).toEqual(expected)
    }
    expect(await read(...whole.split(''))).toEqual(expected)
  })

  it('copes with a multi-byte character split across chunks', async () => {
    const bytes = encoder.encode('data: charging → done\n\n')
    const arrow = bytes.indexOf(0xe2)
    expect(await read(bytes.slice(0, arrow + 1), bytes.slice(arrow + 1))).toEqual([message('message', 'charging → done')])
  })

  it('ignores an id with a NUL, fields it does not know, and a line with no colon', async () => {
    expect(await read('id: a\0b\nretry: 1000\nunknown\ndata: ok\n\n')).toEqual([message('message', 'ok')])
  })

  it('drops a message the stream ended in the middle of', async () => {
    expect(await read('data: complete\n\ndata: cut off')).toEqual([message('message', 'complete')])
  })

  it('ends when the body ends, and releases the body', async () => {
    const stream = body('data: x\n\n')
    for await (const item of readSse(stream)) expect(item.kind).toBe('message')
    expect(stream.locked).toBe(false)
  })

  it('stops reading when the consumer stops', async () => {
    const stream = body('data: 1\n\ndata: 2\n\ndata: 3\n\n')
    for await (const item of readSse(stream)) {
      expect(item).toEqual(message('message', '1'))
      break
    }
    expect(stream.locked).toBe(false)
  })
})
