/**
 * Resilient WebSocket client (framework-free so it can be unit-tested).
 *
 * - Reconnects forever with exponential backoff + jitter (no thundering herd after a deploy).
 * - On every (re)open it re-authenticates and re-subscribes to every room still in use, and tells
 *   listeners to `resync` (re-fetch REST state), because events may have been missed meanwhile.
 * - Detects half-open connections with a ping/watchdog instead of trusting `onclose` alone.
 * - Rooms are reference-counted: two components watching one auction share one subscription.
 */
import type { ServerMessage } from './types'

export type ConnectionStatus = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface SocketLike {
  readyState: number
  send(data: string): void
  close(): void
  onopen: ((ev: unknown) => void) | null
  onmessage: ((ev: { data: unknown }) => void) | null
  onclose: ((ev: { code: number }) => void) | null
  onerror: ((ev: unknown) => void) | null
}

const OPEN = 1
const PING_EVERY_MS = 20_000
const WATCHDOG_EVERY_MS = 10_000
const STALE_AFTER_MS = 55_000
const MAX_BACKOFF_MS = 30_000
const BASE_BACKOFF_MS = 500
const UNAUTHORIZED_CLOSE = 4401

export class RealtimeClient {
  status: ConnectionStatus = 'closed'
  /** Called when the server closes the socket because our token was rejected. */
  onUnauthorized: (() => void) | null = null

  private socket: SocketLike | null = null
  private rooms = new Map<string, number>()
  private listeners = new Set<(m: ServerMessage) => void>()
  private statusListeners = new Set<(s: ConnectionStatus) => void>()
  private token: string | null = null
  private attempt = 0
  private everOpened = false
  private stopped = true
  private lastSeen = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined
  private heartbeat: ReturnType<typeof setInterval> | undefined

  constructor(
    private readonly url: string,
    private readonly createSocket: (url: string) => SocketLike = (u) =>
      new WebSocket(u) as unknown as SocketLike,
    private readonly random: () => number = Math.random,
    private readonly now: () => number = Date.now,
  ) {}

  // ---- lifecycle -------------------------------------------------------------------
  start(): void {
    if (!this.stopped) return
    this.stopped = false
    this.connect()
  }

  stop(): void {
    this.stopped = true
    clearTimeout(this.reconnectTimer)
    clearInterval(this.heartbeat)
    const s = this.socket
    this.socket = null
    if (s) {
      s.onclose = null
      s.close()
    }
    this.setStatus('closed')
  }

  /** Reconnect right now (e.g. the tab just became visible or the network came back). */
  reconnectNow(): void {
    if (this.stopped || this.status === 'open' || this.status === 'connecting') return
    clearTimeout(this.reconnectTimer)
    this.connect()
  }

  // ---- public API ------------------------------------------------------------------
  setToken(token: string | null): void {
    this.token = token
    if (this.isOpen() && token) this.sendJson({ type: 'auth', token })
  }

  subscribe(room: string): () => void {
    const count = this.rooms.get(room) ?? 0
    this.rooms.set(room, count + 1)
    if (count === 0 && this.isOpen()) this.sendJson({ type: 'subscribe', room })
    let released = false
    return () => {
      if (released) return
      released = true
      const n = (this.rooms.get(room) ?? 1) - 1
      if (n <= 0) {
        this.rooms.delete(room)
        if (this.isOpen()) this.sendJson({ type: 'unsubscribe', room })
      } else this.rooms.set(room, n)
    }
  }

  addListener(fn: (m: ServerMessage) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  onStatus(fn: (s: ConnectionStatus) => void): () => void {
    this.statusListeners.add(fn)
    return () => this.statusListeners.delete(fn)
  }

  // ---- internals -------------------------------------------------------------------
  private isOpen(): boolean {
    return this.socket?.readyState === OPEN
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) return
    this.status = status
    this.statusListeners.forEach((l) => l(status))
  }

  private sendJson(msg: unknown): void {
    if (this.isOpen()) this.socket?.send(JSON.stringify(msg))
  }

  private emit(m: ServerMessage): void {
    this.listeners.forEach((l) => l(m))
  }

  private connect(): void {
    this.setStatus(this.everOpened ? 'reconnecting' : 'connecting')
    let socket: SocketLike
    try {
      socket = this.createSocket(this.url)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.socket = socket

    socket.onopen = () => {
      this.attempt = 0
      this.lastSeen = this.now()
      this.setStatus('open')
      if (this.token) this.sendJson({ type: 'auth', token: this.token })
      for (const room of this.rooms.keys()) this.sendJson({ type: 'subscribe', room })
      if (this.everOpened) this.emit({ type: 'resync' }) // we may have missed events
      this.everOpened = true
      this.startHeartbeat()
    }
    socket.onmessage = (ev) => {
      this.lastSeen = this.now()
      try {
        this.emit(JSON.parse(String(ev.data)) as ServerMessage)
      } catch {
        /* ignore malformed frames */
      }
    }
    socket.onclose = (ev) => {
      clearInterval(this.heartbeat)
      this.socket = null
      if (this.stopped) return
      // 4401: the server rejected our token. Let the auth layer mint a fresh one (setToken) and
      // carry on with the normal backoff; the reconnect re-authenticates with whatever it has.
      if (ev.code === UNAUTHORIZED_CLOSE) this.onUnauthorized?.()
      this.scheduleReconnect()
    }
    socket.onerror = () => {
      /* a close event always follows; reconnect logic lives there */
    }
  }

  private scheduleReconnect(): void {
    this.setStatus('reconnecting')
    const ceiling = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** this.attempt)
    const delay = ceiling * (0.5 + this.random() * 0.5) // jitter: 50-100% of the ceiling
    this.attempt += 1
    clearTimeout(this.reconnectTimer)
    this.reconnectTimer = setTimeout(() => this.connect(), delay)
  }

  private startHeartbeat(): void {
    clearInterval(this.heartbeat)
    let sinceWatchdog = 0
    let sincePing = 0
    this.heartbeat = setInterval(() => {
      sinceWatchdog += 1000
      sincePing += 1000
      if (sincePing >= PING_EVERY_MS) {
        sincePing = 0
        this.sendJson({ type: 'ping' })
      }
      if (sinceWatchdog >= WATCHDOG_EVERY_MS) {
        sinceWatchdog = 0
        if (this.now() - this.lastSeen > STALE_AFTER_MS) this.socket?.close() // half-open
      }
    }, 1000)
  }
}
