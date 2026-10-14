/**
 * Server-synchronised time.
 *
 * The browser clock can be minutes off, which would make every countdown wrong and tempt users
 * to "snipe" on a stale deadline. Every API/WebSocket payload carries `server_time`; we keep the
 * measured offset and compute countdowns against `serverNow()`, not `Date.now()`.
 */
import { useSyncExternalStore } from 'react'

let offsetMs = 0
let sampled = false

/** Record a server timestamp seen "now". Smoothed so one slow response can't jolt countdowns. */
export function syncServerTime(serverIso: string): void {
  const server = Date.parse(serverIso)
  if (Number.isNaN(server)) return
  const sample = server - Date.now()
  if (!sampled) {
    offsetMs = sample
    sampled = true
  } else if (Math.abs(sample - offsetMs) > 40) {
    offsetMs = offsetMs * 0.6 + sample * 0.4
  }
}

export function serverNow(): number {
  return Date.now() + offsetMs
}

/** Test hook: reset the module's clock state. */
export function __resetClock(): void {
  offsetMs = 0
  sampled = false
}

// One shared ticker for every countdown on the page (20 cards must not mean 20 timers).
const TICK_MS = 250
let tickValue = Math.floor(serverNow() / TICK_MS)
const subscribers = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | undefined

function subscribe(cb: () => void): () => void {
  subscribers.add(cb)
  if (subscribers.size === 1) {
    timer = setInterval(() => {
      tickValue = Math.floor(serverNow() / TICK_MS)
      subscribers.forEach((s) => s())
    }, TICK_MS)
  }
  return () => {
    subscribers.delete(cb)
    if (subscribers.size === 0 && timer) clearInterval(timer)
  }
}

/** Re-renders the caller ~4x/second and returns the current server time in ms. */
export function useServerNow(): number {
  const tick = useSyncExternalStore(subscribe, () => tickValue)
  return tick * TICK_MS
}

export function msUntil(iso: string, now: number): number {
  return Date.parse(iso) - now
}

/** 93784000 -> "1d 2h", 3725000 -> "1:02:05", 125000 -> "2:05", 9000 -> "0:09". */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  const d = Math.floor(total / 86400)
  const h = Math.floor((total % 86400) / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

const dateTime = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'short',
  hour: 'numeric',
  minute: '2-digit',
})
export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso))
}

export function timeAgo(iso: string, now: number): string {
  const sec = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  if (sec < 5) return 'just now'
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
  return formatDateTime(iso)
}

/** `<input type="datetime-local">` value (local wall time) for a Date. */
export function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}
