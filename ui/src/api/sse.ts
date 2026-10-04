/** One message of a Server-Sent-Events stream, or a comment line (the broker's keepalive). */
export type SseItem = { kind: 'message'; id: string | null; event: string; data: string } | { kind: 'comment'; text: string }

/**
 * Read a Server-Sent-Events body. ``EventSource`` cannot send the API key header (and the key must not
 * go in a URL), so the console reads the stream with ``fetch`` and parses it here, following the
 * format of the HTML standard: fields ``id``/``event``/``data``, ``:`` comments, a blank line ends a
 * message, lines end in LF, CRLF or CR, and a chunk may end anywhere, even between a CR and its LF.
 */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseItem, void, void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let pending = '' // text of an unfinished line
  let id: string | null = null
  let event = ''
  let data: string[] = []

  function* lines(text: string, final: boolean): Generator<string> {
    pending += text
    // A CR at the very end may be the first half of a CRLF; wait for the next chunk to know
    const held = !final && pending.endsWith('\r') ? '\r' : ''
    const complete = (held ? pending.slice(0, -1) : pending).replace(/\r\n|\r/g, '\n')
    const parts = complete.split('\n')
    pending = (final ? '' : parts.pop() ?? '') + held
    if (final && parts[parts.length - 1] === '') parts.pop()
    yield* parts
  }

  function* handle(line: string): Generator<SseItem> {
    if (line === '') {
      if (data.length > 0) yield { kind: 'message', id, event: event || 'message', data: data.join('\n') }
      id = null
      event = ''
      data = []
      return
    }
    if (line.startsWith(':')) {
      yield { kind: 'comment', text: line.slice(1).trim() }
      return
    }
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') data.push(value)
    else if (field === 'event') event = value
    else if (field === 'id' && !value.includes('\0')) id = value
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) {
        for (const line of lines(decoder.decode(), true)) yield* handle(line)
        return
      }
      for (const line of lines(decoder.decode(value, { stream: true }), false)) yield* handle(line)
    }
  } finally {
    reader.releaseLock()
  }
}
