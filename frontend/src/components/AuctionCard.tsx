import { Gauge, MapPin, Users } from 'lucide-react'
import { Link } from 'react-router-dom'
import { BikeImage } from '@/components/BikeImage'
import { Countdown, Price, StatusBadge } from '@/components/auction-bits'
import { Badge, Card, Skeleton } from '@/components/ui/misc'
import { ENDING_SOON_MS } from '@/lib/auctionEvents'
import { formatKm, ordinal } from '@/lib/money'
import { msUntil, useServerNow } from '@/lib/time'
import type { Auction } from '@/lib/types'

export function AuctionCard({ auction }: { auction: Auction }) {
  const now = useServerNow()
  const { bike, viewer } = auction
  const endingSoon = auction.status === 'LIVE' && msUntil(auction.end_at, now) <= ENDING_SOON_MS
  const label =
    auction.status === 'ENDED'
      ? auction.outcome === 'SOLD'
        ? 'Sold for'
        : 'Final bid'
      : auction.bid_count === 0
        ? 'Starting bid'
        : 'Current bid'

  return (
    <Link
      to={`/auctions/${auction.id}`}
      className="group block rounded-2xl focus-visible:outline-offset-4"
      aria-label={`${bike.year} ${bike.make} ${bike.model}, ${label.toLowerCase()}`}
    >
      <Card className="overflow-hidden transition-all group-hover:-translate-y-0.5 group-hover:shadow-lg">
        <div className="relative">
          <BikeImage bike={bike} className="aspect-[16/10]" />
          <div className="absolute top-3 left-3 flex gap-2">
            <StatusBadge auction={auction} />
            {endingSoon && <Badge tone="warning">Ending soon</Badge>}
          </div>
          {(auction.status === 'LIVE' || auction.status === 'SCHEDULED') && (
            <div className="absolute right-3 bottom-3 rounded-full bg-card/90 px-2.5 py-1 text-xs shadow-sm backdrop-blur">
              <Countdown auction={auction} />
            </div>
          )}
        </div>

        <div className="space-y-3 p-4">
          <div>
            <h3 className="line-clamp-1 text-base font-bold">
              {bike.year} {bike.make} {bike.model}
            </h3>
            <p className="line-clamp-1 text-sm text-muted-foreground">
              {bike.variant ?? bike.color ?? ' '}
            </p>
          </div>

          <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <li className="inline-flex items-center gap-1">
              <Gauge className="size-3.5" aria-hidden /> {formatKm(bike.km_driven)}
            </li>
            <li className="inline-flex items-center gap-1">
              <Users className="size-3.5" aria-hidden /> {ordinal(bike.owner_number)}
            </li>
            {bike.registration_state && (
              <li className="inline-flex items-center gap-1">
                <MapPin className="size-3.5" aria-hidden /> {bike.registration_state}
              </li>
            )}
          </ul>

          <div className="flex items-end justify-between border-t border-border pt-3">
            <div>
              <p className="text-xs text-muted-foreground">{label}</p>
              <Price paise={auction.current_price} className="text-xl font-bold" />
            </div>
            <div className="text-right">
              <p className="text-xs text-muted-foreground tabular">
                {auction.bid_count} {auction.bid_count === 1 ? 'bid' : 'bids'}
              </p>
              {viewer?.is_leading && auction.status === 'LIVE' && (
                <Badge tone="success" className="mt-1">
                  You&rsquo;re leading
                </Badge>
              )}
              {viewer && !viewer.is_leading && viewer.my_highest_bid !== null && auction.status === 'LIVE' && (
                <Badge tone="warning" className="mt-1">
                  Outbid
                </Badge>
              )}
            </div>
          </div>
        </div>
      </Card>
    </Link>
  )
}

export function AuctionCardSkeleton() {
  return (
    <Card className="overflow-hidden" aria-hidden>
      <Skeleton className="aspect-[16/10] rounded-none" />
      <div className="space-y-3 p-4">
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="mt-4 h-7 w-1/3" />
      </div>
    </Card>
  )
}
