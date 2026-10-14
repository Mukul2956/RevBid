import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
  type ReactNode,
} from 'react'
import { useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { onAccessTokenChange, getAccessToken } from './api'
import { applyAuctionEvent, prependBid } from './auctionEvents'
import { RealtimeClient, type ConnectionStatus } from './realtimeClient'
import { syncServerTime } from './time'
import type {
  Auction,
  AuctionEvent,
  AuctionWonEvent,
  Bid,
  OutbidEvent,
  Page,
  ServerMessage,
} from './types'
import { useAuth } from './auth'

const WS_URL: string =
  import.meta.env.VITE_WS_URL ??
  `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.hostname}:8100/ws`

const RealtimeContext = createContext<RealtimeClient | null>(null)

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const { refresh } = useAuth()
  const client = useMemo(() => new RealtimeClient(WS_URL), [])

  useEffect(() => {
    client.onUnauthorized = () => void refresh() // gets a fresh token; setToken re-auths
    client.setToken(getAccessToken())
    const offToken = onAccessTokenChange((t) => client.setToken(t))
    client.start()

    const wake = () => {
      if (document.visibilityState === 'visible') client.reconnectNow()
    }
    document.addEventListener('visibilitychange', wake)
    window.addEventListener('online', wake)
    return () => {
      offToken()
      document.removeEventListener('visibilitychange', wake)
      window.removeEventListener('online', wake)
      client.stop()
    }
  }, [client, refresh])

  return <RealtimeContext.Provider value={client}>{children}</RealtimeContext.Provider>
}

export function useRealtime(): RealtimeClient {
  const ctx = useContext(RealtimeContext)
  if (!ctx) throw new Error('useRealtime must be used inside <RealtimeProvider>')
  return ctx
}

export function useConnectionStatus(): ConnectionStatus {
  const client = useRealtime()
  return useSyncExternalStore(
    (cb) => client.onStatus(cb),
    () => client.status,
  )
}

type BidPages = InfiniteData<Page<Bid>, string | null>

/** Keeps one auction's cached detail + bid history live while the component is mounted. */
export function useAuctionLive(auctionId: string | undefined): void {
  const client = useRealtime()
  const qc = useQueryClient()

  useEffect(() => {
    if (!auctionId) return
    const release = client.subscribe(`auction:${auctionId}`)
    const off = client.addListener((msg: ServerMessage) => {
      if (msg.type === 'resync') {
        void qc.invalidateQueries({ queryKey: ['auction', auctionId] })
        void qc.invalidateQueries({ queryKey: ['bids', auctionId] })
        return
      }
      if (!('auction_id' in msg) || msg.auction_id !== auctionId) return
      if (msg.type === 'outbid' || msg.type === 'auction_won') return
      syncServerTime(msg.server_time)
      qc.setQueryData<Auction>(['auction', auctionId], (prev) =>
        prev ? applyAuctionEvent(prev, msg) : prev,
      )
      if (msg.type === 'bid_placed') {
        qc.setQueryData<BidPages>(['bids', auctionId], (prev) => {
          if (!prev) return prev
          const [first, ...rest] = prev.pages
          if (!first) return prev
          return {
            ...prev,
            pages: [{ ...first, items: prependBid(first.items, msg.data.bid) }, ...rest],
          }
        })
      }
    })
    return () => {
      off()
      release()
    }
  }, [client, qc, auctionId])
}

type AuctionPages = InfiniteData<Page<Auction>, string | null>

/** Keeps list pages live: patch prices/timers in place, refetch when membership changes. */
export function useLobbyLive(): void {
  const client = useRealtime()
  const qc = useQueryClient()

  useEffect(() => {
    const release = client.subscribe('lobby')
    const off = client.addListener((msg) => {
      if (msg.type === 'resync') {
        void qc.invalidateQueries({ queryKey: ['auctions'] })
        return
      }
      if (!('auction_id' in msg) || msg.type === 'outbid' || msg.type === 'auction_won') return
      syncServerTime(msg.server_time)
      const event: AuctionEvent = msg
      if (event.type === 'bid_placed') {
        qc.setQueriesData<AuctionPages>({ queryKey: ['auctions'] }, (prev) =>
          prev
            ? {
                ...prev,
                pages: prev.pages.map((p) => ({
                  ...p,
                  items: p.items.map((a) =>
                    a.id === event.auction_id ? applyAuctionEvent(a, event) : a,
                  ),
                })),
              }
            : prev,
        )
      } else {
        // started / ended / cancelled / updated change which tab an auction belongs to
        void qc.invalidateQueries({ queryKey: ['auctions'] })
      }
    })
    return () => {
      off()
      release()
    }
  }, [client, qc])
}

/** Private events (outbid / won) for the signed-in user. The latest handler is always used. */
export function useRealtimeNotifications(onEvent: (event: OutbidEvent | AuctionWonEvent) => void): void {
  const client = useRealtime()
  const qc = useQueryClient()
  const handler = useRef(onEvent)
  useEffect(() => {
    handler.current = onEvent
  }, [onEvent])

  useEffect(
    () =>
      client.addListener((msg) => {
        if (msg.type !== 'outbid' && msg.type !== 'auction_won') return
        handler.current(msg)
        void qc.invalidateQueries({ queryKey: ['my-bids'] })
      }),
    [client, qc],
  )
}
