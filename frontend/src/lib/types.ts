/** Wire types. They mirror backend/app/schemas; money is always integer paise. */

export type AuctionStatus = 'DRAFT' | 'SCHEDULED' | 'LIVE' | 'ENDED' | 'CANCELLED'
export type Outcome = 'SOLD' | 'UNSOLD'
export type Role = 'BIDDER' | 'ADMIN'
export type Condition = 'EXCELLENT' | 'GOOD' | 'FAIR'

export interface User {
  id: string
  email: string
  display_name: string
  role: Role
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export interface Bike {
  id: string
  make: string
  model: string
  variant: string | null
  year: number
  km_driven: number
  owner_number: number
  engine_cc: number | null
  color: string | null
  registration_state: string | null
  condition: Condition
  inspection_score: number | null
  description: string
  images: string[]
  created_at: string
}

export type BikeInput = Omit<Bike, 'id' | 'created_at'>

export interface Viewer {
  alias: string
  is_leading: boolean
  my_highest_bid: number | null
}

export interface Auction {
  id: string
  status: AuctionStatus
  outcome: Outcome | null
  cancel_reason: string | null
  start_at: string
  end_at: string
  starting_price: number
  current_price: number
  min_increment: number
  min_next_bid: number
  bid_count: number
  has_reserve: boolean
  reserve_met: boolean | null
  leader_alias: string | null
  winner_alias: string | null
  version: number
  server_time: string
  bike: Bike
  viewer: Viewer | null
}

export interface AdminAuction extends Auction {
  reserve_price: number | null
  leader_id: string | null
  winner_id: string | null
  original_end_at: string
  snipe_window_s: number
  snipe_extension_s: number
  max_extension_s: number
  created_at: string
}

export interface AuctionCreateInput {
  bike_id: string
  start_at: string
  end_at: string
  starting_price: number
  reserve_price: number | null
  min_increment: number
  snipe_window_s?: number
  snipe_extension_s?: number
  max_extension_s?: number
  schedule: boolean
}

export interface Bid {
  id: string
  amount: number
  created_at: string
  bidder_alias: string
}

export interface BidAccepted {
  bid: Bid
  auction: Auction
  extended: boolean
}

export type MyBidResult = 'LEADING' | 'OUTBID' | 'WON' | 'LOST' | 'CANCELLED'

export interface MyBid {
  auction: Auction
  my_highest_bid: number
  last_bid_at: string
  result: MyBidResult
}

export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

export interface AdminStats {
  users: number
  auctions_by_status: Record<AuctionStatus, number>
  total_bids: number
  bids_last_hour: number
  sold_count: number
  unsold_count: number
  gross_sold_value: number
  server_time: string
}

export interface ComponentHealth {
  ok: boolean
  latency_ms: number | null
  error: string | null
}

export interface SystemInfo {
  version: string
  env: string
  uptime_s: number
  database: ComponentHealth
  redis: ComponentHealth
  websocket_connections: number
  websocket_rooms: number
  scheduler_enabled: boolean
  scheduler_last_tick_age_s: number | null
  scheduler_last_error: string | null
  bids_last_minute: number
  demo_mode: boolean
}

export interface AuctionAuditEvent {
  id: number
  type: string
  actor_id: string | null
  payload: Record<string, unknown>
  created_at: string
}

// ---- realtime ------------------------------------------------------------------------
interface EventBase<T extends string, D> {
  type: T
  auction_id: string
  version: number
  server_time: string
  data: D
}

export type BidPlacedEvent = EventBase<
  'bid_placed',
  {
    current_price: number
    min_next_bid: number
    bid_count: number
    end_at: string
    reserve_met: boolean | null
    leader_alias: string
    extended: boolean
    bid: Bid
  }
>
export type AuctionStartedEvent = EventBase<'auction_started', { status: 'LIVE'; end_at: string }>
export type AuctionEndedEvent = EventBase<
  'auction_ended',
  { status: 'ENDED'; outcome: Outcome; final_price: number; winner_alias: string | null }
>
export type AuctionCancelledEvent = EventBase<
  'auction_cancelled',
  { status: 'CANCELLED'; reason: string | null }
>
export type AuctionUpdatedEvent = EventBase<'auction_updated', Omit<Auction, 'viewer'>>
export type OutbidEvent = EventBase<
  'outbid',
  { title: string; your_amount: number | null; current_price: number; min_next_bid: number }
>
export type AuctionWonEvent = EventBase<'auction_won', { title: string; final_price: number }>

export type AuctionEvent =
  | BidPlacedEvent
  | AuctionStartedEvent
  | AuctionEndedEvent
  | AuctionCancelledEvent
  | AuctionUpdatedEvent
export type PrivateEvent = OutbidEvent | AuctionWonEvent
export type ServerEvent = AuctionEvent | PrivateEvent

export type ControlMessage =
  | { type: 'authed'; user_id: string }
  | { type: 'subscribed' | 'unsubscribed'; room: string }
  | { type: 'pong' }
  | { type: 'resync' }
  | { type: 'error'; code: string }

export type ServerMessage = ServerEvent | ControlMessage
