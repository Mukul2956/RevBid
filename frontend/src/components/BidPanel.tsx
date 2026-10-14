import { Gavel, ShieldAlert, Trophy } from 'lucide-react'
import { useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { toast } from 'sonner'
import { ConnectionBadge, Countdown, Price, StatusBadge } from '@/components/auction-bits'
import { Button } from '@/components/ui/button'
import { Modal } from '@/components/ui/dialog'
import { Alert, Badge, Card } from '@/components/ui/misc'
import { ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/cn'
import { formatINR, formatRupees, rupeesToPaise } from '@/lib/money'
import { usePlaceBid } from '@/lib/queries'
import { formatDateTime, msUntil, useServerNow } from '@/lib/time'
import type { Auction } from '@/lib/types'

/** Bids at or above this multiple of the current price ask for confirmation (fat-finger guard). */
const CONFIRM_MULTIPLE = 2

export function bidErrorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return 'Something went wrong. Please try again.'
  switch (err.code) {
    case 'BID_TOO_LOW': {
      const floor = err.num('min_next_bid')
      return floor
        ? `Someone just bid higher. The minimum is now ${formatINR(floor)}.`
        : 'Your bid is below the minimum.'
    }
    case 'ALREADY_HIGHEST':
      return 'You are already the highest bidder.'
    case 'AUCTION_ENDED':
      return 'This auction has just ended.'
    case 'AUCTION_NOT_LIVE':
      return 'Bidding has not opened yet.'
    case 'BID_TOO_HIGH':
      return 'That amount is above the maximum allowed bid.'
    case 'RATE_LIMITED':
      return `You're bidding too fast. Try again in ${err.num('retry_after') ?? 'a few'}s.`
    case 'ADMIN_CANNOT_BID':
      return 'Administrator accounts cannot place bids.'
    case 'NETWORK_ERROR':
      return err.message
    default:
      return err.message
  }
}

export function BidPanel({ auction }: { auction: Auction }) {
  const { user, isAdmin } = useAuth()
  const now = useServerNow()
  const location = useLocation()
  const bid = usePlaceBid(auction.id)

  const live = auction.status === 'LIVE' && msUntil(auction.end_at, now) > 0
  const floor = auction.min_next_bid

  const [typed, setTyped] = useState('')
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<number | null>(null)

  // The field follows the live minimum until the user types their own number (derived, no effect).
  const text = dirty ? typed : formatRupees(floor)
  const amount = rupeesToPaise(text)
  const belowFloor = amount !== null && amount < floor

  const chips = useMemo(
    () =>
      [0, 1, 2, 5].map((n) => ({
        label: n === 0 ? 'Min' : `+${n}×`,
        value: floor + n * auction.min_increment,
      })),
    [floor, auction.min_increment],
  )

  // One idempotency key per *attempt*: a retried request reuses it, a new click mints a new one.
  const attemptKey = useRef<{ amount: number; key: string } | null>(null)

  function submit(paise: number) {
    setError(null)
    if (attemptKey.current?.amount !== paise) {
      attemptKey.current = { amount: paise, key: crypto.randomUUID() }
    }
    bid.mutate(
      { amount: paise, key: attemptKey.current.key },
      {
        onSuccess: (res) => {
          attemptKey.current = null
          setDirty(false)
          toast.success(`Bid of ${formatINR(res.bid.amount)} placed`, {
            description: res.extended ? 'Closing time extended by anti-sniping.' : undefined,
          })
        },
        onError: (err) => {
          const known = err instanceof ApiError && err.status !== 0 && err.status < 500
          if (known) attemptKey.current = null // a definite answer: next click is a new attempt
          setError(bidErrorMessage(err))
          setDirty(false) // snap back to the (possibly updated) minimum
        },
      },
    )
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (amount === null) return setError('Enter a whole-rupee amount, e.g. 85,000.')
    if (amount < floor) return setError(`Minimum bid is ${formatINR(floor)}.`)
    if (amount >= auction.current_price * CONFIRM_MULTIPLE && auction.bid_count > 0) {
      return setConfirming(amount)
    }
    submit(amount)
  }

  const viewer = auction.viewer
  const iWon = auction.status === 'ENDED' && auction.outcome === 'SOLD' && viewer?.alias === auction.winner_alias
  const priceLabel =
    auction.status === 'ENDED'
      ? auction.outcome === 'SOLD'
        ? 'Sold for'
        : 'Final bid'
      : auction.bid_count === 0
        ? 'Starting bid'
        : 'Current bid'

  return (
    <Card className="space-y-5 p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <StatusBadge auction={auction} />
        {auction.status === 'LIVE' && <ConnectionBadge />}
      </div>

      <div>
        <p className="text-sm text-muted-foreground">{priceLabel}</p>
        <div className="flex flex-wrap items-baseline gap-x-3">
          <Price paise={auction.current_price} className="text-4xl font-extrabold" />
          <span className="text-sm text-muted-foreground tabular">
            {auction.bid_count} {auction.bid_count === 1 ? 'bid' : 'bids'}
          </span>
        </div>
        {/* Announced politely to screen readers whenever the price changes. */}
        <p className="sr-only" aria-live="polite">
          Current bid {formatINR(auction.current_price)}, {auction.bid_count} bids.
        </p>
        {auction.has_reserve && (auction.status === 'LIVE' || auction.status === 'SCHEDULED') && (
          <Badge tone={auction.reserve_met ? 'success' : 'warning'} className="mt-2">
            {auction.reserve_met ? 'Reserve met' : 'Reserve not met yet'}
          </Badge>
        )}
      </div>

      {auction.status === 'LIVE' && (
        <div>
          <p className="text-sm text-muted-foreground">Time left</p>
          <Countdown auction={auction} size="lg" />
        </div>
      )}

      {viewer?.is_leading && auction.status === 'LIVE' && (
        <Alert tone="success" title="You're the highest bidder">
          Your bid of {formatINR(viewer.my_highest_bid ?? auction.current_price)} is leading.
        </Alert>
      )}
      {viewer && !viewer.is_leading && viewer.my_highest_bid !== null && auction.status === 'LIVE' && (
        <Alert tone="warning" title="You've been outbid">
          Your highest bid was {formatINR(viewer.my_highest_bid)}.
        </Alert>
      )}

      {/* ---- the bid form -------------------------------------------------------------- */}
      {live && user && !isAdmin && (
        <form onSubmit={onSubmit} className="space-y-3" noValidate>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Quick bid amounts">
            {chips.map((c) => (
              <button
                key={c.label}
                type="button"
                onClick={() => {
                  setTyped(formatRupees(c.value))
                  setDirty(true)
                  setError(null)
                }}
                className={cn(
                  'cursor-pointer rounded-lg border px-3 py-1.5 text-sm font-semibold tabular transition-colors',
                  amount === c.value
                    ? 'border-primary bg-info-soft text-primary'
                    : 'border-border hover:bg-muted',
                )}
              >
                {formatINR(c.value)}
                <span className="ml-1 text-xs font-normal text-muted-foreground">{c.label}</span>
              </button>
            ))}
          </div>

          <div>
            <label htmlFor="bid-amount" className="sr-only">
              Your bid in rupees
            </label>
            <div className="flex items-center rounded-xl border border-border bg-card focus-within:border-primary">
              <span className="pl-4 text-lg font-semibold text-muted-foreground" aria-hidden>
                ₹
              </span>
              <input
                id="bid-amount"
                inputMode="numeric"
                autoComplete="off"
                value={text}
                aria-invalid={belowFloor || !!error}
                aria-describedby="bid-help"
                onChange={(e) => {
                  setTyped(e.target.value.replace(/[^\d,]/g, ''))
                  setDirty(true)
                  setError(null)
                }}
                onBlur={() => amount !== null && setTyped(formatRupees(amount))}
                className="h-12 w-full bg-transparent px-2 text-lg font-bold tabular outline-none"
              />
            </div>
            <p id="bid-help" className="mt-1.5 text-xs text-muted-foreground">
              Minimum {formatINR(floor)} · increments of {formatINR(auction.min_increment)}
            </p>
          </div>

          {(error || belowFloor) && (
            <p role="alert" className="text-sm font-medium text-danger">
              {error ?? `Minimum bid is ${formatINR(floor)}.`}
            </p>
          )}

          <Button type="submit" size="lg" className="w-full" loading={bid.isPending} disabled={amount === null}>
            <Gavel className="size-5" aria-hidden />
            Place bid{amount !== null && ` · ${formatINR(amount)}`}
          </Button>
        </form>
      )}

      {live && !user && (
        <div className="space-y-2">
          <Link
            to={`/login?next=${encodeURIComponent(location.pathname)}`}
            className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-primary text-base font-semibold text-primary-foreground hover:bg-primary-hover"
          >
            <Gavel className="size-5" aria-hidden /> Log in to bid
          </Link>
          <p className="text-center text-xs text-muted-foreground">
            New here?{' '}
            <Link to="/register" className="font-semibold text-primary underline">
              Create a free account
            </Link>
          </p>
        </div>
      )}

      {live && isAdmin && (
        <Alert tone="info" title="Administrators can't bid">
          Sign in with a bidder account to take part in this auction.
        </Alert>
      )}

      {/* ---- non-live states ----------------------------------------------------------- */}
      {auction.status === 'SCHEDULED' && (
        <Alert tone="info" title="Bidding opens soon">
          Starts {formatDateTime(auction.start_at)}. Come back then, or open this page and it will go live automatically.
        </Alert>
      )}
      {auction.status === 'ENDED' && (
        <Alert
          tone={iWon ? 'success' : 'info'}
          title={iWon ? 'You won this auction' : auction.outcome === 'SOLD' ? 'Auction ended: sold' : 'Auction ended: not sold'}
        >
          {iWon ? (
            <span className="inline-flex items-center gap-1.5">
              <Trophy className="size-4" aria-hidden /> Congratulations! The team will contact you to complete the purchase.
            </span>
          ) : auction.outcome === 'SOLD' ? (
            `Sold to ${auction.winner_alias} for ${formatINR(auction.current_price)}.`
          ) : auction.bid_count > 0 ? (
            'The highest bid did not meet the seller’s reserve price.'
          ) : (
            'No bids were placed.'
          )}
        </Alert>
      )}
      {auction.status === 'CANCELLED' && (
        <Alert tone="warning" title="Auction cancelled">
          <span className="inline-flex items-center gap-1.5">
            <ShieldAlert className="size-4" aria-hidden />
            {auction.cancel_reason ?? 'This auction was withdrawn.'}
          </span>
        </Alert>
      )}

      <Modal
        open={confirming !== null}
        onOpenChange={(o) => !o && setConfirming(null)}
        title="Confirm a large bid"
        description={`You're about to bid ${confirming ? formatINR(confirming) : ''}, more than double the current price of ${formatINR(auction.current_price)}. Bids can't be withdrawn.`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirming(null)}>
              Go back
            </Button>
            <Button
              onClick={() => {
                const v = confirming
                setConfirming(null)
                if (v !== null) submit(v)
              }}
            >
              Yes, place bid
            </Button>
          </>
        }
      />
    </Card>
  )
}
