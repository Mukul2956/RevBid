import { Clock, Wifi, WifiOff } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge } from '@/components/ui/misc'
import { ENDING_SOON_MS, FINAL_MINUTE_MS } from '@/lib/auctionEvents'
import { cn } from '@/lib/cn'
import { formatINR } from '@/lib/money'
import { useConnectionStatus } from '@/lib/realtime'
import { formatCountdown, msUntil, useServerNow } from '@/lib/time'
import type { Auction } from '@/lib/types'

/** Small building blocks shared by cards, the detail page and the admin console. */

export function StatusBadge({ auction }: { auction: Pick<Auction, 'status' | 'outcome'> }) {
  switch (auction.status) {
    case 'LIVE':
      return (
        <Badge tone="danger">
          <span className="live-dot inline-block size-1.5 rounded-full bg-danger" aria-hidden />
          LIVE
        </Badge>
      )
    case 'SCHEDULED':
      return <Badge tone="primary">Upcoming</Badge>
    case 'DRAFT':
      return <Badge>Draft</Badge>
    case 'CANCELLED':
      return <Badge tone="warning">Cancelled</Badge>
    case 'ENDED':
      return auction.outcome === 'SOLD' ? (
        <Badge tone="success">Sold</Badge>
      ) : (
        <Badge>Unsold</Badge>
      )
  }
}

/**
 * Time left (or until start). Driven by server time, not the device clock, and turns urgent
 * in the last 5 minutes / final minute so the deadline is felt before it is missed.
 */
export function Countdown({
  auction,
  size = 'sm',
  className,
}: {
  auction: Pick<Auction, 'status' | 'start_at' | 'end_at'>
  size?: 'sm' | 'lg'
  className?: string
}) {
  const now = useServerNow()
  if (auction.status === 'SCHEDULED') {
    return (
      <span className={cn('inline-flex items-center gap-1 tabular', className)} role="timer">
        <Clock className="size-3.5" aria-hidden />
        Starts in {formatCountdown(msUntil(auction.start_at, now))}
      </span>
    )
  }
  if (auction.status !== 'LIVE') return null

  const left = msUntil(auction.end_at, now)
  if (left <= 0) {
    return <span className={cn('font-semibold text-muted-foreground', className)}>Closing…</span>
  }
  const tone =
    left <= FINAL_MINUTE_MS
      ? 'text-danger'
      : left <= ENDING_SOON_MS
        ? 'text-warning'
        : 'text-foreground'
  return (
    <span
      role="timer"
      aria-label={`Time left ${formatCountdown(left)}`}
      className={cn(
        'inline-flex items-center gap-1 font-semibold tabular',
        size === 'lg' && 'text-3xl font-bold',
        tone,
        className,
      )}
    >
      <Clock className={size === 'lg' ? 'size-6' : 'size-3.5'} aria-hidden />
      {formatCountdown(left)}
    </span>
  )
}

/** A price that flashes when it goes up, so a glance catches a new bid. */
export function Price({ paise, className }: { paise: number; className?: string }) {
  const previous = useRef(paise)
  const [flash, setFlash] = useState(0)

  useEffect(() => {
    if (paise > previous.current) setFlash((n) => n + 1)
    previous.current = paise
  }, [paise])

  return (
    <span
      // re-keying restarts the CSS animation on every increase
      key={flash}
      className={cn('inline-block px-1 tabular', flash > 0 && 'price-flash', className)}
    >
      {formatINR(paise)}
    </span>
  )
}

export function ConnectionBadge() {
  const status = useConnectionStatus()
  const open = status === 'open'
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-xs font-medium',
        open ? 'text-success' : 'text-warning',
      )}
      title={open ? 'Receiving live updates' : 'Reconnecting to live updates…'}
    >
      {open ? <Wifi className="size-3.5" aria-hidden /> : <WifiOff className="size-3.5" aria-hidden />}
      {open ? 'Live updates on' : status === 'closed' ? 'Offline' : 'Reconnecting…'}
    </span>
  )
}

export function InspectionScore({ score }: { score: number }) {
  const tone = score >= 85 ? 'bg-success' : score >= 70 ? 'bg-primary' : 'bg-warning'
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-medium">Inspection score</span>
        <span className="font-bold tabular">{score}/100</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted" role="img" aria-label={`Inspection score ${score} out of 100`}>
        <div className={cn('h-full rounded-full', tone)} style={{ width: `${score}%` }} />
      </div>
    </div>
  )
}
