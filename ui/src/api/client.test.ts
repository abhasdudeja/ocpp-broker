import { describe, expect, it, vi } from 'vitest'

import { ApiError, apiGet, forgetKey, isAbort, loadKey, saveKey } from './client'
import { mockFetch, respond } from '../test-utils'

describe('apiGet', () => {
  it('sends the key in X-API-Key and never in the URL, and returns the JSON body', async () => {
    const fetch = mockFetch(() => respond({ ok: 1 }))
    expect(await apiGet<{ ok: number }>('/api/system/info', 'secret')).toEqual({ ok: 1 })
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/system/info')
    expect(init?.headers).toMatchObject({ 'X-API-Key': 'secret' })
    expect(String(url)).not.toContain('secret')
    expect(init?.cache).toBe('no-store')
  })

  it('turns an error status into an ApiError carrying the broker detail', async () => {
    mockFetch(() => respond({ detail: 'Missing or invalid API key' }, 401))
    await expect(apiGet('/api/x', 'k')).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      message: 'Missing or invalid API key',
    })
  })

  it('shows structured details (a 422 list) as text', async () => {
    mockFetch(() => respond({ detail: [{ loc: ['body', 'x'], msg: 'bad' }] }, 422))
    const error = await apiGet('/api/x', 'k').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toContain('"msg":"bad"')
  })

  it('falls back to the status text when the body is not JSON', async () => {
    mockFetch(() => new Response('<html>', { status: 502, statusText: 'Bad Gateway' }))
    await expect(apiGet('/api/x', 'k')).rejects.toMatchObject({ status: 502, message: 'Bad Gateway' })
  })

  it('reports an unreachable broker as status 0', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
    await expect(apiGet('/api/x', 'k')).rejects.toMatchObject({ status: 0, message: 'Could not reach the broker' })
  })

  it('lets an abort through unchanged, so callers can tell it from a failure', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new DOMException('aborted', 'AbortError'))))
    const error = await apiGet('/api/x', 'k').catch((e: unknown) => e)
    expect(isAbort(error)).toBe(true)
  })
})

describe('key storage', () => {
  it('keeps the key in sessionStorage only', () => {
    saveKey('abc')
    expect(loadKey()).toBe('abc')
    expect(window.sessionStorage.getItem('ocpp-broker-api-key')).toBe('abc')
    expect(window.localStorage.length).toBe(0)
    forgetKey()
    expect(loadKey()).toBeNull()
  })

  it('does not throw when storage is unavailable', () => {
    const blocked = () => {
      throw new DOMException('denied', 'SecurityError')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(blocked)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(blocked)
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(blocked)
    expect(() => saveKey('abc')).not.toThrow()
    expect(loadKey()).toBeNull()
    expect(() => forgetKey()).not.toThrow()
  })
})
