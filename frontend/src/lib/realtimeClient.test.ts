import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RealtimeClient, type SocketLike } from './realtimeClient'
import type { ServerMessage } from './types'

class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = []
  readyState = 0
  sent: unknown[] = []
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  constructor(readonly url: string) {
    FakeSocket.instances.push(this)
  }
  send(data: string) {
    this.sent.push(JSON.parse(data))
  }
  close() {
    this.readyState = 3
    this.onclose?.({ code: 1000 })
  }
  // test helpers
  open() {
    this.readyState = 1
    this.onopen?.({})
  }
  receive(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }
  drop(code = 1006) {
    this.readyState = 3
    this.onclose?.({ code })
  }
}

const last = () => FakeSocket.instances.at(-1)!

function make(random = () => 1) {
  const client = new RealtimeClient('ws://test/ws', (u) => new FakeSocket(u), random)
  const messages: ServerMessage[] = []
  client.addListener((m) => messages.push(m))
  return { client, messages }
}

describe('RealtimeClient', () => {
  beforeEach(() => {
    FakeSocket.instances = []
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it('authenticates and subscribes on open, in that order', () => {
    const { client } = make()
    client.setToken('jwt-1')
    client.subscribe('lobby')
    client.start()
    last().open()
    expect(last().sent).toEqual([{ type: 'auth', token: 'jwt-1' }, { type: 'subscribe', room: 'lobby' }])
    expect(client.status).toBe('open')
  })

  it('reference-counts rooms: one server subscription shared by many consumers', () => {
    const { client } = make()
    client.start()
    last().open()
    const a = client.subscribe('auction:1')
    const b = client.subscribe('auction:1')
    expect(last().sent.filter((m) => (m as { type: string }).type === 'subscribe')).toHaveLength(1)
    a()
    expect(last().sent.some((m) => (m as { type: string }).type === 'unsubscribe')).toBe(false)
    b()
    b() // double release is harmless
    expect(last().sent.filter((m) => (m as { type: string }).type === 'unsubscribe')).toHaveLength(1)
  })

  it('re-authenticates an open socket when the token changes', () => {
    const { client } = make()
    client.start()
    last().open()
    client.setToken('fresh')
    expect(last().sent).toContainEqual({ type: 'auth', token: 'fresh' })
  })

  it('reconnects with exponential, jittered backoff and resets after success', () => {
    const { client } = make(() => 1) // jitter factor 1.0 -> delay == ceiling
    client.start()
    expect(FakeSocket.instances).toHaveLength(1)
    last().drop()
    expect(client.status).toBe('reconnecting')

    vi.advanceTimersByTime(499)
    expect(FakeSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(1) // 500ms
    expect(FakeSocket.instances).toHaveLength(2)

    last().drop()
    vi.advanceTimersByTime(999)
    expect(FakeSocket.instances).toHaveLength(2)
    vi.advanceTimersByTime(1) // 1000ms
    expect(FakeSocket.instances).toHaveLength(3)

    last().open() // success resets the attempt counter
    last().drop()
    vi.advanceTimersByTime(500)
    expect(FakeSocket.instances).toHaveLength(4)
  })

  it('caps the backoff at 30 seconds', () => {
    const { client } = make(() => 1)
    client.start()
    for (let i = 0; i < 12; i++) {
      last().drop()
      vi.advanceTimersByTime(30_000)
    }
    const before = FakeSocket.instances.length
    last().drop()
    vi.advanceTimersByTime(29_999)
    expect(FakeSocket.instances).toHaveLength(before)
    vi.advanceTimersByTime(1)
    expect(FakeSocket.instances).toHaveLength(before + 1)
  })

  it('jitter spreads reconnects (never exceeds the ceiling, never below half)', () => {
    const { client } = make(() => 0) // minimum jitter -> half the ceiling
    client.start()
    last().drop()
    vi.advanceTimersByTime(249)
    expect(FakeSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeSocket.instances).toHaveLength(2)
    void client
  })

  it('re-subscribes after a reconnect and tells listeners to resync (but not on first open)', () => {
    const { client, messages } = make()
    client.setToken('t')
    client.subscribe('auction:9')
    client.start()
    last().open()
    expect(messages).toEqual([]) // first connect: nothing to resync

    last().drop()
    vi.advanceTimersByTime(500)
    last().open()

    expect(last().sent).toEqual([{ type: 'auth', token: 't' }, { type: 'subscribe', room: 'auction:9' }])
    expect(messages).toEqual([{ type: 'resync' }])
  })

  it('delivers parsed messages and survives malformed frames', () => {
    const { client, messages } = make()
    client.start()
    last().open()
    last().receive({ type: 'pong' })
    last().onmessage?.({ data: '{not json' })
    last().receive({ type: 'resync' })
    expect(messages).toEqual([{ type: 'pong' }, { type: 'resync' }])
  })

  it('asks the auth layer for a fresh token when the server rejects ours (4401)', () => {
    const { client } = make()
    const onUnauthorized = vi.fn()
    client.onUnauthorized = onUnauthorized
    client.start()
    last().open()
    last().drop(4401)
    expect(onUnauthorized).toHaveBeenCalledOnce()
    vi.advanceTimersByTime(500)
    expect(FakeSocket.instances).toHaveLength(2) // and it still reconnects
  })

  it('detects a half-open connection (no traffic) and forces a reconnect', () => {
    let now = 1_000_000
    const client = new RealtimeClient('ws://test/ws', (u) => new FakeSocket(u), () => 1, () => now)
    client.start()
    last().open()
    now += 70_000 // 70 s of silence, well past the staleness threshold
    vi.advanceTimersByTime(10_000) // watchdog tick
    expect(client.status).toBe('reconnecting')
  })

  it('sends keep-alive pings', () => {
    const { client } = make()
    client.start()
    last().open()
    vi.advanceTimersByTime(20_000)
    expect(last().sent).toContainEqual({ type: 'ping' })
  })

  it('stop() is final: no reconnect after it', () => {
    const { client } = make()
    client.start()
    last().open()
    client.stop()
    vi.advanceTimersByTime(120_000)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(client.status).toBe('closed')
  })

  it('reconnectNow() skips the backoff wait', () => {
    const { client } = make()
    client.start()
    last().drop()
    client.reconnectNow()
    expect(FakeSocket.instances).toHaveLength(2)
  })
})
