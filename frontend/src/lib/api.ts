/**
 * Typed fetch wrapper for the REST API.
 *
 * - Attaches the in-memory access token; on a 401 it refreshes once (single-flight, see auth.tsx)
 *   and retries the request, so an expired token is invisible to the user.
 * - Turns RFC 7807 problem responses into `ApiError` carrying the stable `code`, which the UI
 *   switches on (never on the human-readable message).
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly extra: Record<string, unknown> = {},
    readonly requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** Numeric detail (e.g. `min_next_bid`) attached by the server, if present. */
  num(key: string): number | undefined {
    const v = this.extra[key]
    return typeof v === 'number' ? v : undefined
  }
}

const BASE = '/api/v1'

let accessToken: string | null = null
const tokenListeners = new Set<(t: string | null) => void>()

export function setAccessToken(token: string | null): void {
  accessToken = token
  tokenListeners.forEach((l) => l(token))
}
export function getAccessToken(): string | null {
  return accessToken
}
export function onAccessTokenChange(listener: (t: string | null) => void): () => void {
  tokenListeners.add(listener)
  return () => tokenListeners.delete(listener)
}

type RefreshFn = () => Promise<string | null>
let refreshSession: RefreshFn | null = null
export function registerRefresh(fn: RefreshFn | null): void {
  refreshSession = fn
}

type QueryValue = string | number | boolean | null | undefined | readonly (string | number)[]

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, QueryValue>
  headers?: Record<string, string>
  /** Send the bearer token and refresh-and-retry on 401 (default true). */
  auth?: boolean
  signal?: AbortSignal
}

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) value.forEach((v) => params.append(key, String(v)))
    else params.set(key, String(value))
  }
  const qs = params.toString()
  return `${BASE}${path}${qs ? `?${qs}` : ''}`
}

async function send(path: string, opts: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json', ...opts.headers }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (opts.auth !== false && accessToken) headers['Authorization'] = `Bearer ${accessToken}`
  try {
    return await fetch(buildUrl(path, opts.query), {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      credentials: 'include',
      signal: opts.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'NETWORK_ERROR', 'Network error. Check your connection and try again.')
  }
}

// Standard problem+json members; everything else is a domain detail (e.g. min_next_bid).
const PROBLEM_MEMBERS = new Set(['type', 'title', 'status', 'code', 'detail', 'request_id', 'errors'])

async function toError(res: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {}
  try {
    body = (await res.json()) as Record<string, unknown>
  } catch {
    /* non-JSON error body (e.g. a proxy 502) */
  }
  const extra = Object.fromEntries(Object.entries(body).filter(([k]) => !PROBLEM_MEMBERS.has(k)))
  if (body['errors']) extra['errors'] = body['errors']
  const { code, detail, request_id } = body
  return new ApiError(
    res.status,
    typeof code === 'string' ? code : 'HTTP_ERROR',
    typeof detail === 'string' && detail ? detail : `Request failed (${res.status})`,
    extra,
    typeof request_id === 'string' ? request_id : undefined,
  )
}

export async function api<T = void>(path: string, opts: RequestOptions = {}): Promise<T> {
  let res = await send(path, opts)

  if (res.status === 401 && opts.auth !== false && refreshSession) {
    const refreshed = await refreshSession()
    if (refreshed) res = await send(path, opts)
  }

  if (!res.ok) throw await toError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Field-level messages from a 422 (`errors: [{field, message}]`), keyed by field name. */
export function fieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError) || !Array.isArray(err.extra['errors'])) return {}
  const out: Record<string, string> = {}
  for (const e of err.extra['errors'] as { field: string; message: string }[]) {
    out[e.field] = e.message
  }
  return out
}
