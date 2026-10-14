/** Server-state hooks (TanStack Query). Query keys are the cache contract used by realtime.tsx. */
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query'
import { api, ApiError } from './api'
import { prependBid } from './auctionEvents'
import { syncServerTime } from './time'
import type {
  AdminAuction,
  AdminStats,
  Auction,
  AuctionAuditEvent,
  AuctionCreateInput,
  AuctionStatus,
  Bid,
  BidAccepted,
  Bike,
  BikeInput,
  MyBid,
  Page,
  SystemInfo,
} from './types'

export type SortKey = 'ending_soon' | 'newest' | 'price_low' | 'price_high'
export type PublicStatus = 'LIVE' | 'SCHEDULED' | 'ENDED'

export interface AuctionFilters {
  status: PublicStatus
  q?: string
  make?: string
  sort: SortKey
  price_min?: number
  price_max?: number
  year_min?: number
}

export function useAuctions(filters: AuctionFilters) {
  return useInfiniteQuery({
    queryKey: ['auctions', filters],
    queryFn: async ({ pageParam, signal }) => {
      const page = await api<Page<Auction>>('/auctions', {
        query: { ...filters, limit: 12, cursor: pageParam },
        signal,
      })
      if (page.items[0]) syncServerTime(page.items[0].server_time)
      return page
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    placeholderData: keepPreviousData,
  })
}

export function useMakes() {
  return useQuery({
    queryKey: ['makes'],
    queryFn: () => api<string[]>('/auctions/makes'),
    staleTime: 5 * 60_000,
  })
}

export function useAuction(id: string | undefined) {
  return useQuery({
    queryKey: ['auction', id],
    enabled: !!id,
    queryFn: async ({ signal }) => {
      const a = await api<Auction>(`/auctions/${id}`, { signal })
      syncServerTime(a.server_time)
      return a
    },
    // Live updates arrive over the socket; polling is only the safety net for a dead socket.
    refetchInterval: 30_000,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  })
}

export function useBids(id: string | undefined) {
  return useInfiniteQuery({
    queryKey: ['bids', id],
    enabled: !!id,
    queryFn: ({ pageParam, signal }) =>
      api<Page<Bid>>(`/auctions/${id}/bids`, { query: { limit: 20, cursor: pageParam }, signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  })
}

export function useMyBids(enabled: boolean) {
  return useQuery({
    queryKey: ['my-bids'],
    enabled,
    queryFn: () => api<MyBid[]>('/me/bids'),
  })
}

type BidPages = InfiniteData<Page<Bid>, string | null>

/**
 * Place a bid. One Idempotency-Key per user intent: a network retry of the *same* click reuses
 * it (the server then returns the original bid instead of creating a second one).
 */
export function usePlaceBid(auctionId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ amount, key }: { amount: number; key: string }) =>
      api<BidAccepted>(`/auctions/${auctionId}/bids`, {
        method: 'POST',
        body: { amount },
        headers: { 'Idempotency-Key': key },
      }),
    retry: (count, err) => err instanceof ApiError && err.status === 0 && count < 2,
    onSuccess: (res) => {
      syncServerTime(res.auction.server_time)
      qc.setQueryData<Auction>(['auction', auctionId], (prev) =>
        !prev || res.auction.version >= prev.version ? res.auction : prev,
      )
      qc.setQueryData<BidPages>(['bids', auctionId], (prev) => {
        const first = prev?.pages[0]
        if (!prev || !first) return prev
        return {
          ...prev,
          pages: [{ ...first, items: prependBid(first.items, res.bid) }, ...prev.pages.slice(1)],
        }
      })
      void qc.invalidateQueries({ queryKey: ['my-bids'] })
    },
    onError: (err) => {
      // Lost a race: refresh the floor so the form shows the new minimum, not a stale one.
      if (err instanceof ApiError) {
        const floor = err.num('min_next_bid')
        if (floor !== undefined) {
          qc.setQueryData<Auction>(['auction', auctionId], (prev) =>
            prev ? { ...prev, min_next_bid: Math.max(prev.min_next_bid, floor) } : prev,
          )
        }
        if (['AUCTION_ENDED', 'AUCTION_NOT_LIVE', 'BID_TOO_LOW'].includes(err.code)) {
          void qc.invalidateQueries({ queryKey: ['auction', auctionId] })
        }
      }
    },
  })
}

// ---- admin --------------------------------------------------------------------------
export function useAdminStats() {
  return useQuery({
    queryKey: ['admin', 'stats'],
    queryFn: () => api<AdminStats>('/admin/stats'),
    refetchInterval: 10_000,
  })
}

export function useAdminSystem() {
  return useQuery({
    queryKey: ['admin', 'system'],
    queryFn: () => api<SystemInfo>('/admin/system'),
    refetchInterval: 5_000,
  })
}

export function useAdminAuctions(status: AuctionStatus | 'ALL') {
  return useInfiniteQuery({
    queryKey: ['admin', 'auctions', status],
    queryFn: ({ pageParam }) =>
      api<Page<AdminAuction>>('/admin/auctions', {
        query: { status: status === 'ALL' ? undefined : status, limit: 25, cursor: pageParam },
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  })
}

export function useAdminAuction(id: string | undefined) {
  return useQuery({
    queryKey: ['admin', 'auction', id],
    enabled: !!id,
    queryFn: () => api<AdminAuction>(`/admin/auctions/${id}`),
    refetchInterval: 15_000,
  })
}

export function useAuditTrail(id: string | undefined) {
  return useQuery({
    queryKey: ['admin', 'events', id],
    enabled: !!id,
    queryFn: () => api<AuctionAuditEvent[]>(`/admin/auctions/${id}/events`),
    refetchInterval: 10_000,
  })
}

export function useAdminBikes(q: string, limit = 25) {
  return useInfiniteQuery({
    queryKey: ['admin', 'bikes', q, limit],
    queryFn: ({ pageParam }) =>
      api<Page<Bike>>('/admin/bikes', { query: { q, limit, cursor: pageParam } }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  })
}

function useInvalidateAdmin() {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: ['admin'] })
    void qc.invalidateQueries({ queryKey: ['auctions'] })
  }
}

export function useCreateAuction() {
  const invalidate = useInvalidateAdmin()
  return useMutation({
    mutationFn: (input: AuctionCreateInput) =>
      api<AdminAuction>('/admin/auctions', { method: 'POST', body: input }),
    onSuccess: invalidate,
  })
}

export function useAuctionAction(id: string) {
  const invalidate = useInvalidateAdmin()
  return useMutation({
    mutationFn: (action: { type: 'schedule' } | { type: 'cancel'; reason: string }) =>
      action.type === 'schedule'
        ? api<AdminAuction>(`/admin/auctions/${id}/schedule`, { method: 'POST' })
        : api<AdminAuction>(`/admin/auctions/${id}/cancel`, {
            method: 'POST',
            body: { reason: action.reason },
          }),
    onSuccess: invalidate,
  })
}

export function useSaveBike(id?: string) {
  const invalidate = useInvalidateAdmin()
  return useMutation({
    mutationFn: (input: BikeInput) =>
      id
        ? api<Bike>(`/admin/bikes/${id}`, { method: 'PATCH', body: input })
        : api<Bike>('/admin/bikes', { method: 'POST', body: input }),
    onSuccess: invalidate,
  })
}

export function useDeleteBike() {
  const invalidate = useInvalidateAdmin()
  return useMutation({
    mutationFn: (id: string) => api(`/admin/bikes/${id}`, { method: 'DELETE' }),
    onSuccess: invalidate,
  })
}
