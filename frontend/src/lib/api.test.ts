import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, buildUrl, fieldErrors, registerRefresh, setAccessToken } from './api'

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })

describe('buildUrl', () => {
  it('serialises query params, skipping empties and repeating arrays', () => {
    expect(buildUrl('/auctions', { status: ['LIVE', 'ENDED'], q: '', make: undefined, limit: 12, x: null })).toBe(
      '/api/v1/auctions?status=LIVE&status=ENDED&limit=12',
    )
    expect(buildUrl('/time')).toBe('/api/v1/time')
  })
})

describe('api()', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    setAccessToken(null)
    registerRefresh(null)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('sends the bearer token and JSON body', async () => {
    setAccessToken('tok')
    fetchMock.mockResolvedValue(json(200, { ok: true }))
    await api('/x', { method: 'POST', body: { a: 1 } })
    const [, init] = fetchMock.mock.calls[0]!
    expect((init?.headers as Record<string, string>)['Authorization']).toBe('Bearer tok')
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json')
    expect(init?.body).toBe('{"a":1}')
  })

  it('turns problem+json into an ApiError carrying the stable code and extras', async () => {
    fetchMock.mockResolvedValue(
      json(409, { code: 'BID_TOO_LOW', detail: 'too low', min_next_bid: 5150000, request_id: 'r1', status: 409, title: 'Conflict', type: 'x' }),
    )
    const err = await api('/bid').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 409, code: 'BID_TOO_LOW', message: 'too low', requestId: 'r1' })
    expect((err as ApiError).num('min_next_bid')).toBe(5150000)
    expect((err as ApiError).extra).not.toHaveProperty('title')
  })

  it('handles non-JSON error bodies (e.g. a proxy 502)', async () => {
    fetchMock.mockResolvedValue(new Response('<html>Bad gateway</html>', { status: 502 }))
    await expect(api('/x')).rejects.toMatchObject({ status: 502, code: 'HTTP_ERROR' })
  })

  it('maps a dropped connection to NETWORK_ERROR', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(api('/x')).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' })
  })

  it('returns undefined for 204', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(api('/x', { method: 'DELETE' })).resolves.toBeUndefined()
  })

  it('refreshes once on 401 and retries with the new token, invisibly to the caller', async () => {
    setAccessToken('expired')
    registerRefresh(async () => {
      setAccessToken('fresh')
      return 'fresh'
    })
    fetchMock
      .mockResolvedValueOnce(json(401, { code: 'INVALID_TOKEN', detail: 'expired' }))
      .mockResolvedValueOnce(json(200, { ok: 1 }))

    await expect(api('/secure')).resolves.toEqual({ ok: 1 })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const retryHeaders = fetchMock.mock.calls[1]![1]?.headers as Record<string, string>
    expect(retryHeaders['Authorization']).toBe('Bearer fresh')
  })

  it('surfaces the 401 if refreshing fails (and does not loop)', async () => {
    setAccessToken('expired')
    registerRefresh(async () => null)
    fetchMock.mockResolvedValue(json(401, { code: 'INVALID_TOKEN', detail: 'expired' }))
    await expect(api('/secure')).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('never refreshes for auth:false calls (login/refresh themselves)', async () => {
    const refresh = vi.fn(async () => 'x')
    registerRefresh(refresh)
    fetchMock.mockResolvedValue(json(401, { code: 'INVALID_CREDENTIALS', detail: 'no' }))
    await expect(api('/auth/login', { method: 'POST', auth: false })).rejects.toMatchObject({ code: 'INVALID_CREDENTIALS' })
    expect(refresh).not.toHaveBeenCalled()
  })
})

describe('fieldErrors', () => {
  it('extracts per-field messages from a 422', () => {
    const err = new ApiError(422, 'VALIDATION_ERROR', 'bad', { errors: [{ field: 'email', message: 'invalid' }] })
    expect(fieldErrors(err)).toEqual({ email: 'invalid' })
    expect(fieldErrors(new Error('x'))).toEqual({})
  })
})
