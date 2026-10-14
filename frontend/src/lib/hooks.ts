import { useEffect, useState } from 'react'

/** Value that only updates after `delay` ms of quiet: keeps typing from hammering the API. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(id)
  }, [value, delay])
  return debounced
}

/** Only allow same-site relative redirects (blocks `?next=https://evil.example`). */
export function safeNext(next: string | null): string {
  return next && next.startsWith('/') && !next.startsWith('//') ? next : '/'
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    const previous = document.title
    document.title = `${title} | RevBid`
    return () => {
      document.title = previous
    }
  }, [title])
}
