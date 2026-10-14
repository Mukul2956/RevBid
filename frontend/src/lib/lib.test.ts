import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { applyAuctionEvent, prependBid } from './auctionEvents'
import { formatINR, formatINRCompact, rupeesToPaise } from './money'
import { __resetClock, formatCountdown, serverNow, syncServerTime, timeAgo } from './time'
import type { Auction, AuctionEvent, Bid, Bike } from './types'

describe('money', () => {
  it('formats rupees with Indian digit grouping', () => {
    expect(formatINR(12_500_000)).toBe('₹1,25,000')
    expect(formatINR(5_250_000)).toBe('₹52,500')
    expect(formatINR(0)).toBe('₹0')
  })

  it('formats compact lakh/crore amounts', () => {
    expect(formatINRCompact(12_500_000)).toBe('₹1.25 L')
    expect(formatINRCompact(1_500_000_000)).toBe('₹1.5 Cr')
    expect(formatINRCompact(5_000_000)).toBe('₹50,000')
  })

  it.each([
    ['85,000', 8_500_000],
    ['  1,25,000 ', 12_500_000],
    ['₹ 52500', 5_250_000],
    ['0', 0],
  ])('parses %j to paise', (input, expected) => {
    expect(rupeesToPaise(input)).toBe(expected)
  })

  it.each(['', 'abc', '12.5', '-5', '1e5', '99999999999'])('rejects %j', (input) => {
    expect(rupeesToPaise(input)).toBeNull()
  })
})

describe('countdown formatting', () => {
  it.each([
    [0, '0:00'],
    [-5_000, '0:00'],
    [9_000, '0:09'],
    [125_000, '2:05'],
    [3_725_000, '1:02:05'],
    [93_784_000, '1d 2h'],
    [500, '0:01'], // rounds up: never show 0:00 while time remains
  ])('%i ms -> %s', (ms, expected) => {
    expect(formatCountdown(ms)).toBe(expected)
  })
})

describe('server time sync', () => {
  beforeEach(() => {
    __resetClock()
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2030-01-01T12:00:00Z'))
  })
  afterEach(() => vi.useRealTimers())

  it('corrects for a wrong device clock', () => {
    // Server says it is 12:05, the device thinks 12:00: offset is +5 minutes.
    syncServerTime('2030-01-01T12:05:00Z')
    expect(serverNow()).toBe(Date.parse('2030-01-01T12:05:00Z'))
    vi.advanceTimersByTime(30_000)
    expect(serverNow()).toBe(Date.parse('2030-01-01T12:05:30Z'))
  })

  it('smooths later samples instead of jumping', () => {
    syncServerTime('2030-01-01T12:00:00Z')
    syncServerTime('2030-01-01T12:00:01Z') // +1s sample
    const offset = serverNow() - Date.now()
    expect(offset).toBeGreaterThan(0)
    expect(offset).toBeLessThan(1000)
  })

  it('ignores garbage timestamps', () => {
    syncServerTime('not a date')
    expect(serverNow()).toBe(Date.now())
  })

  it('renders relative times', () => {
    const now = Date.parse('2030-01-01T12:00:00Z')
    expect(timeAgo('2030-01-01T11:59:58Z', now)).toBe('just now')
    expect(timeAgo('2030-01-01T11:59:30Z', now)).toBe('30s ago')
    expect(timeAgo('2030-01-01T11:50:00Z', now)).toBe('10m ago')
    expect(timeAgo('2030-01-01T09:00:00Z', now)).toBe('3h ago')
  })
})

// ---- realtime event reducer ------------------------------------------------------------
const bike: Bike = {
  id: 'b1', make: 'KTM', model: 'Duke 390', variant: null, year: 2018, km_driven: 1, owner_number: 1,
  engine_cc: null, color: null, registration_state: null, condition: 'GOOD', inspection_score: null,
  description: '', images: [], created_at: '2030-01-01T00:00:00Z',
}

function auction(over: Partial<Auction> = {}): Auction {
  return {
    id: 'a1', status: 'LIVE', outcome: null, cancel_reason: null,
    start_at: '2030-01-01T11:00:00Z', end_at: '2030-01-01T13:00:00Z',
    starting_price: 5_000_000, current_price: 5_000_000, min_increment: 50_000, min_next_bid: 5_000_000,
    bid_count: 0, has_reserve: true, reserve_met: false, leader_alias: null, winner_alias: null,
    version: 3, server_time: '2030-01-01T12:00:00Z', bike: { ...bike }, viewer: null, ...over,
  }
}

const bid = (amount: number, alias = 'Bidder-AAAAA'): Bid => ({
  id: `bid-${amount}`, amount, created_at: '2030-01-01T12:00:01Z', bidder_alias: alias,
})

function bidEvent(version: number, amount: number, alias: string): AuctionEvent {
  return {
    type: 'bid_placed', auction_id: 'a1', version, server_time: '2030-01-01T12:00:01Z',
    data: {
      current_price: amount, min_next_bid: amount + 50_000, bid_count: 1, end_at: '2030-01-01T13:02:00Z',
      reserve_met: true, leader_alias: alias, extended: true, bid: bid(amount, alias),
    },
  }
}

describe('applyAuctionEvent', () => {
  it('applies a newer bid and extends the end time', () => {
    const next = applyAuctionEvent(auction(), bidEvent(4, 5_100_000, 'Bidder-RIVAL'))
    expect(next).toMatchObject({
      version: 4, current_price: 5_100_000, min_next_bid: 5_150_000, bid_count: 1,
      leader_alias: 'Bidder-RIVAL', end_at: '2030-01-01T13:02:00Z', reserve_met: true,
    })
  })

  it('ignores stale and duplicate events (at-most-once, unordered delivery)', () => {
    const current = auction({ version: 7, current_price: 9_000_000 })
    expect(applyAuctionEvent(current, bidEvent(7, 5_100_000, 'x'))).toBe(current)
    expect(applyAuctionEvent(current, bidEvent(6, 5_100_000, 'x'))).toBe(current)
  })

  it('tracks whether the viewer is leading, from the alias alone', () => {
    const mine = auction({ viewer: { alias: 'Bidder-ME', is_leading: false, my_highest_bid: 5_000_000 } })
    const leading = applyAuctionEvent(mine, bidEvent(4, 5_100_000, 'Bidder-ME'))
    expect(leading.viewer).toEqual({ alias: 'Bidder-ME', is_leading: true, my_highest_bid: 5_100_000 })
    const outbid = applyAuctionEvent(leading, bidEvent(5, 5_200_000, 'Bidder-RIVAL'))
    expect(outbid.viewer).toEqual({ alias: 'Bidder-ME', is_leading: false, my_highest_bid: 5_100_000 })
  })

  it('handles started / ended / cancelled', () => {
    const scheduled = auction({ status: 'SCHEDULED' })
    const started = applyAuctionEvent(scheduled, {
      type: 'auction_started', auction_id: 'a1', version: 4, server_time: 't',
      data: { status: 'LIVE', end_at: '2030-01-01T14:00:00Z' },
    })
    expect(started).toMatchObject({ status: 'LIVE', end_at: '2030-01-01T14:00:00Z' })

    const ended = applyAuctionEvent(started, {
      type: 'auction_ended', auction_id: 'a1', version: 5, server_time: 't',
      data: { status: 'ENDED', outcome: 'SOLD', final_price: 7_000_000, winner_alias: 'Bidder-W' },
    })
    expect(ended).toMatchObject({ status: 'ENDED', outcome: 'SOLD', current_price: 7_000_000, winner_alias: 'Bidder-W' })

    const cancelled = applyAuctionEvent(auction(), {
      type: 'auction_cancelled', auction_id: 'a1', version: 4, server_time: 't',
      data: { status: 'CANCELLED', reason: 'Seller withdrew' },
    })
    expect(cancelled).toMatchObject({ status: 'CANCELLED', cancel_reason: 'Seller withdrew' })
  })

  it('merges an admin edit snapshot but keeps the viewer-specific fields', () => {
    const { viewer: _v, ...snapshot } = auction({ version: 9, starting_price: 6_000_000, leader_alias: 'Bidder-ME' })
    void _v
    const mine = auction({ viewer: { alias: 'Bidder-ME', is_leading: false, my_highest_bid: null } })
    const next = applyAuctionEvent(mine, { type: 'auction_updated', auction_id: 'a1', version: 9, server_time: 't', data: snapshot })
    expect(next.starting_price).toBe(6_000_000)
    expect(next.viewer).toEqual({ alias: 'Bidder-ME', is_leading: true, my_highest_bid: null })
  })
})

describe('prependBid', () => {
  it('keeps history newest-first, de-duplicated', () => {
    const list = [bid(300), bid(200)]
    expect(prependBid(list, bid(400)).map((b) => b.amount)).toEqual([400, 300, 200])
    expect(prependBid(list, bid(300))).toBe(list)
  })
})
