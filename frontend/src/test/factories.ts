import type { Auction } from '@/lib/types'

export function makeAuction(over: Partial<Auction> = {}): Auction {
  const future = new Date(Date.now() + 30 * 60_000).toISOString()
  return {
    id: 'auction-1',
    status: 'LIVE',
    outcome: null,
    cancel_reason: null,
    start_at: new Date(Date.now() - 60 * 60_000).toISOString(),
    end_at: future,
    starting_price: 5_000_000,
    current_price: 5_000_000,
    min_increment: 50_000,
    min_next_bid: 5_000_000,
    bid_count: 0,
    has_reserve: false,
    reserve_met: null,
    leader_alias: null,
    winner_alias: null,
    version: 1,
    server_time: new Date().toISOString(),
    viewer: null,
    bike: {
      id: 'bike-1',
      make: 'KTM',
      model: 'Duke 390',
      variant: null,
      year: 2018,
      km_driven: 28_900,
      owner_number: 2,
      engine_cc: 373,
      color: 'Orange',
      registration_state: 'Karnataka',
      condition: 'GOOD',
      inspection_score: 76,
      description: '',
      images: [],
      created_at: new Date().toISOString(),
    },
    ...over,
  }
}
