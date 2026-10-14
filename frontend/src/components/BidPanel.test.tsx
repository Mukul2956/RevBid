import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeAuction } from '@/test/factories'
import type { Auction, User } from '@/lib/types'
import { BidPanel } from './BidPanel'

const auth = vi.hoisted(() => ({ user: null as User | null, isAdmin: false }))
vi.mock('@/lib/auth', () => ({ useAuth: () => auth }))
vi.mock('@/lib/realtime', () => ({ useConnectionStatus: () => 'open' }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }))
vi.mock('sonner', () => ({ toast }))

const fetchMock = vi.fn<typeof fetch>()
const bidder: User = { id: 'u1', email: 'b@x.dev', display_name: 'Bidder', role: 'BIDDER' }

function renderPanel(auction: Auction): ReactElement | void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(['auction', auction.id], auction)
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <BidPanel auction={auction} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const ok = (body: unknown, status = 201) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function accepted(a: Auction, amount: number) {
  return {
    bid: { id: 'bid-1', amount, created_at: new Date().toISOString(), bidder_alias: 'Bidder-ME' },
    auction: { ...a, current_price: amount, bid_count: 1, version: a.version + 1, min_next_bid: amount + a.min_increment,
      viewer: { alias: 'Bidder-ME', is_leading: true, my_highest_bid: amount } },
    extended: false,
  }
}

describe('BidPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    toast.success.mockReset()
    auth.user = bidder
    auth.isAdmin = false
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows the live price and prefills the minimum next bid', () => {
    renderPanel(makeAuction({ bid_count: 3, current_price: 5_200_000, min_next_bid: 5_250_000 }))
    expect(screen.getByText('Current bid')).toBeInTheDocument()
    expect(screen.getAllByText('₹52,000').length).toBeGreaterThan(0)
    expect(screen.getByLabelText('Your bid in rupees')).toHaveValue('52,500')
    expect(screen.getByRole('button', { name: /Place bid · ₹52,500/ })).toBeEnabled()
  })

  it('offers quick-bid chips that fill the field', async () => {
    renderPanel(makeAuction())
    await userEvent.click(screen.getByRole('button', { name: /\+2×/ }))
    expect(screen.getByLabelText('Your bid in rupees')).toHaveValue('51,000')
    expect(screen.getByRole('button', { name: /Place bid · ₹51,000/ })).toBeInTheDocument()
  })

  it('places a bid with an Idempotency-Key and confirms to the user', async () => {
    const a = makeAuction()
    fetchMock.mockResolvedValue(ok(accepted(a, 5_000_000)))
    renderPanel(a)

    await userEvent.click(screen.getByRole('button', { name: /Place bid/ }))

    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/v1/auctions/auction-1/bids')
    expect(JSON.parse(String(init?.body))).toEqual({ amount: 5_000_000 })
    expect((init?.headers as Record<string, string>)['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('explains a lost race with the new minimum instead of a generic error', async () => {
    fetchMock.mockResolvedValue(
      ok({ code: 'BID_TOO_LOW', detail: 'low', min_next_bid: 5_150_000, status: 409 }, 409),
    )
    renderPanel(makeAuction())

    await userEvent.click(screen.getByRole('button', { name: /Place bid/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Someone just bid higher. The minimum is now ₹51,500.')
  })

  it('refuses amounts below the minimum without calling the API', async () => {
    renderPanel(makeAuction())
    const input = screen.getByLabelText('Your bid in rupees')
    await userEvent.clear(input)
    await userEvent.type(input, '100')
    expect(screen.getByRole('alert')).toHaveTextContent('Minimum bid is ₹50,000.')
    await userEvent.click(screen.getByRole('button', { name: /Place bid/ }))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('asks for confirmation on a bid more than double the current price (fat-finger guard)', async () => {
    const a = makeAuction({ bid_count: 2, current_price: 5_000_000, min_next_bid: 5_050_000 })
    fetchMock.mockResolvedValue(ok(accepted(a, 10_000_000)))
    renderPanel(a)
    const input = screen.getByLabelText('Your bid in rupees')
    await userEvent.clear(input)
    await userEvent.type(input, '100000')

    await userEvent.click(screen.getByRole('button', { name: /Place bid/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('more than double')
    expect(fetchMock).not.toHaveBeenCalled()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Go back' }))
    expect(fetchMock).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /Place bid/ }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Yes, place bid' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
  })

  it('tells a leading bidder they are winning, and an outbid one how to respond', () => {
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <BidPanel auction={makeAuction({ bid_count: 1, viewer: { alias: 'A', is_leading: true, my_highest_bid: 5_000_000 } })} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(screen.getByText("You're the highest bidder")).toBeInTheDocument()
    unmount()
    renderPanel(makeAuction({ bid_count: 2, viewer: { alias: 'A', is_leading: false, my_highest_bid: 5_000_000 } }))
    expect(screen.getByText("You've been outbid")).toBeInTheDocument()
  })

  it('invites anonymous visitors to log in, preserving where they were', () => {
    auth.user = null
    renderPanel(makeAuction())
    const link = screen.getByRole('link', { name: /Log in to bid/ })
    expect(link).toHaveAttribute('href', expect.stringContaining('/login?next='))
    expect(screen.queryByLabelText('Your bid in rupees')).not.toBeInTheDocument()
  })

  it('does not let administrators bid', () => {
    auth.user = { ...bidder, role: 'ADMIN' }
    auth.isAdmin = true
    renderPanel(makeAuction())
    expect(screen.getByText("Administrators can't bid")).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Place bid/ })).not.toBeInTheDocument()
  })

  it('shows the outcome of finished auctions', () => {
    renderPanel(makeAuction({ status: 'ENDED', outcome: 'SOLD', winner_alias: 'Bidder-ME', bid_count: 4,
      viewer: { alias: 'Bidder-ME', is_leading: true, my_highest_bid: 5_000_000 } }))
    expect(screen.getByText('You won this auction')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Place bid/ })).not.toBeInTheDocument()
  })

  it('explains an unsold auction whose reserve was not met', () => {
    renderPanel(makeAuction({ status: 'ENDED', outcome: 'UNSOLD', bid_count: 2, has_reserve: true }))
    expect(screen.getByText('Auction ended: not sold')).toBeInTheDocument()
    expect(screen.getByText(/did not meet the seller/)).toBeInTheDocument()
  })

  it('shows reserve status without ever showing the reserve amount', () => {
    renderPanel(makeAuction({ has_reserve: true, reserve_met: false }))
    expect(screen.getByText('Reserve not met yet')).toBeInTheDocument()
  })
})
