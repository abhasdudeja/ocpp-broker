import type { components } from './schema'

// Types come from the broker's OpenAPI schema (ui/openapi.json, regenerated with `npm run api`),
// so a change to the API that the console does not follow fails the type check.
export type SystemInfo = components['schemas']['SystemInfo']
export type OrgSummary = components['schemas']['OrgSummary']
export type ChargerList = components['schemas']['ChargerList']
export type ChargerSummary = components['schemas']['ChargerSummary']
export type ChargerDetail = components['schemas']['ChargerDetail']
export type BackendLink = components['schemas']['BackendLink']
export type TransactionRow = components['schemas']['TransactionRow']
export type BrokerEvent = components['schemas']['ConsoleEvent']
export type CommandCatalog = components['schemas']['CommandCatalog']
export type CommandSpec = components['schemas']['CommandSpecInfo']
export type CommandHistory = components['schemas']['CommandHistory']
export type CommandLogEntry = components['schemas']['CommandLogEntry']

const KEY_STORAGE = 'ocpp-broker-api-key'

/** A request that did not succeed. ``status`` is 0 when the broker could not be reached at all. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// The key is kept for the life of the tab only (sessionStorage), never in the URL or localStorage.
// Storage can be unavailable (private windows, blocked site data): then the key lives in memory only.
export function loadKey(): string | null {
  try {
    return window.sessionStorage.getItem(KEY_STORAGE)
  } catch {
    return null
  }
}

export function saveKey(key: string): void {
  try {
    window.sessionStorage.setItem(KEY_STORAGE, key)
  } catch {
    // memory only
  }
}

export function forgetKey(): void {
  try {
    window.sessionStorage.removeItem(KEY_STORAGE)
  } catch {
    // nothing stored
  }
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail
      return typeof detail === 'string' ? detail : JSON.stringify(detail)
    }
  } catch {
    // not JSON
  }
  return response.statusText || `HTTP ${response.status}`
}

/** ``path`` with the given query parameters; empty ones are left out. Values are encoded, never concatenated. */
export function withQuery(path: string, params: Record<string, string | undefined>): string {
  const query = new URLSearchParams()
  for (const [name, value] of Object.entries(params)) {
    if (value) query.set(name, value)
  }
  const text = query.toString()
  return text ? `${path}?${text}` : path
}

/** GET a JSON resource from the broker, authenticated with the API key. */
export async function apiGet<T>(path: string, key: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      headers: { 'X-API-Key': key, Accept: 'application/json' },
      cache: 'no-store',
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'Could not reach the broker')
  }
  if (!response.ok) throw new ApiError(response.status, await errorDetail(response))
  return (await response.json()) as T
}

/**
 * POST JSON to the broker. Besides 2xx, the statuses in ``accept`` are returned as data instead of thrown
 * (a command that timed out is answered with 504 and a body that says so).
 */
export async function apiPost<T>(
  path: string,
  key: string,
  body: unknown,
  options: { signal?: AbortSignal; accept?: number[] } = {},
): Promise<{ status: number; body: T }> {
  let response: Response
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: { 'X-API-Key': key, Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'Could not reach the broker')
  }
  if (!response.ok && !options.accept?.includes(response.status)) throw new ApiError(response.status, await errorDetail(response))
  return { status: response.status, body: (await response.json()) as T }
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}
