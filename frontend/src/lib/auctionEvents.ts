/**
 * Pure reducers that fold realtime events into cached data.
 *
 * Delivery is at-most-once and unordered across publishers, so every event carries the
 * auction's monotonically increasing `version`: an event not newer than what we hold is ignored,
 * which makes duplicates, reordering and replays harmless.
 */
import type { Auction, AuctionEvent, Bid } from './types'

export function applyAuctionEvent(auction: Auction, event: AuctionEvent): Auction {
  if (event.version <= auction.version) return auction // stale or duplicate

  const base = { ...auction, version: event.version, server_time: event.server_time }
  const viewerAlias = auction.viewer?.alias

  switch (event.type) {
    case 'bid_placed': {
      const d = event.data
      const mine = viewerAlias !== undefined && d.leader_alias === viewerAlias
      return {
        ...base,
        current_price: d.current_price,
        min_next_bid: d.min_next_bid,
        bid_count: d.bid_count,
        end_at: d.end_at,
        reserve_met: d.reserve_met,
        leader_alias: d.leader_alias,
        viewer: auction.viewer
          ? {
              ...auction.viewer,
              is_leading: mine,
              my_highest_bid: mine
                ? Math.max(auction.viewer.my_highest_bid ?? 0, d.bid.amount)
                : auction.viewer.my_highest_bid,
            }
          : null,
      }
    }
    case 'auction_started':
      return { ...base, status: 'LIVE', end_at: event.data.end_at }
    case 'auction_ended':
      return {
        ...base,
        status: 'ENDED',
        outcome: event.data.outcome,
        current_price: event.data.final_price,
        winner_alias: event.data.winner_alias,
      }
    case 'auction_cancelled':
      return { ...base, status: 'CANCELLED', cancel_reason: event.data.reason }
    case 'auction_updated': {
      const snapshot = event.data
      return {
        ...auction,
        ...snapshot,
        // the snapshot is viewer-agnostic: recompute who is leading from the alias we know
        viewer: auction.viewer
          ? { ...auction.viewer, is_leading: snapshot.leader_alias === auction.viewer.alias }
          : null,
      }
    }
  }
}

/** Prepend a bid to a newest-first history, ignoring ones we already have. */
export function prependBid(items: Bid[], bid: Bid): Bid[] {
  if (items.some((b) => b.id === bid.id)) return items
  return [bid, ...items].sort((a, b) => b.amount - a.amount)
}

/** Ending within this window gets the "ending soon" treatment in the UI. */
export const ENDING_SOON_MS = 5 * 60 * 1000
export const FINAL_MINUTE_MS = 60 * 1000
