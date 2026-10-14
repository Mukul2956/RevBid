import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api, ApiError, registerRefresh, setAccessToken } from './api'
import type { TokenResponse, User } from './types'

/**
 * Session model: a short-lived access token lives only in memory (nothing for XSS to steal from
 * storage); a rotating httpOnly refresh cookie restores it. `rb_session` is a non-secret hint so
 * anonymous visitors don't fire a pointless refresh (and a console 401) on every page load.
 */
const SESSION_HINT = 'rb_session'
const REFRESH_MARGIN_S = 60

type Status = 'loading' | 'authenticated' | 'anonymous'

interface AuthContextValue {
  user: User | null
  status: Status
  isAdmin: boolean
  login: (email: string, password: string) => Promise<User>
  register: (input: { email: string; password: string; display_name: string }) => Promise<User>
  logout: () => Promise<void>
  /** Mint a fresh access token from the refresh cookie (null if the session is gone). */
  refresh: () => Promise<string | null>
}

const AuthContext = createContext<AuthContextValue | null>(null)

function hasHint(): boolean {
  try {
    return localStorage.getItem(SESSION_HINT) === '1'
  } catch {
    return false
  }
}
function setHint(on: boolean): void {
  try {
    if (on) localStorage.setItem(SESSION_HINT, '1')
    else localStorage.removeItem(SESSION_HINT)
  } catch {
    /* storage unavailable (private mode): the session just won't auto-restore */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [status, setStatus] = useState<Status>(() => (hasHint() ? 'loading' : 'anonymous'))
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const inflight = useRef<Promise<string | null> | null>(null)
  // The scheduled refresh must call the *latest* refresh without making apply/refresh circular.
  const refreshRef = useRef<() => Promise<string | null>>(async () => null)

  const clearSession = useCallback(() => {
    clearTimeout(timer.current)
    setAccessToken(null)
    setHint(false)
    setUser(null)
    setStatus('anonymous')
  }, [])

  const apply = useCallback((t: TokenResponse) => {
    setAccessToken(t.access_token)
    setUser(t.user)
    setStatus('authenticated')
    setHint(true)
    clearTimeout(timer.current)
    const delay = Math.max(5, t.expires_in - REFRESH_MARGIN_S) * 1000
    timer.current = setTimeout(() => void refreshRef.current(), delay)
  }, [])

  const refresh = useCallback(async (): Promise<string | null> => {
    // Single-flight: concurrent 401s share one refresh (the token rotates, so racing would
    // present a spent token). navigator.locks extends that guarantee across browser tabs.
    if (inflight.current) return inflight.current
    const run = async (): Promise<string | null> => {
      try {
        const t = await api<TokenResponse>('/auth/refresh', { method: 'POST', auth: false })
        apply(t)
        return t.access_token
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) clearSession()
        return null
      }
    }
    const pending = navigator.locks ? navigator.locks.request('rb-refresh', run) : run()
    inflight.current = pending.finally(() => {
      inflight.current = null
    })
    return inflight.current
  }, [apply, clearSession])

  useEffect(() => {
    refreshRef.current = refresh
  }, [refresh])

  useEffect(() => {
    registerRefresh(refresh)
    if (hasHint()) void refresh().then((tok) => tok ?? setStatus('anonymous'))
    return () => {
      registerRefresh(null)
      clearTimeout(timer.current)
    }
  }, [refresh])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      status,
      refresh,
      isAdmin: user?.role === 'ADMIN',
      async login(email, password) {
        const t = await api<TokenResponse>('/auth/login', {
          method: 'POST',
          body: { email, password },
          auth: false,
        })
        apply(t)
        return t.user
      },
      async register(input) {
        const t = await api<TokenResponse>('/auth/register', {
          method: 'POST',
          body: input,
          auth: false,
        })
        apply(t)
        return t.user
      },
      async logout() {
        try {
          await api('/auth/logout', { method: 'POST', auth: false })
        } finally {
          clearSession()
        }
      },
    }),
    [user, status, refresh, apply, clearSession],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
