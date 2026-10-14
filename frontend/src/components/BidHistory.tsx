import { Crown } from 'lucide-react'
import { Badge, Card, EmptyState, Skeleton } from '@/components/ui/misc'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/cn'
import { formatINR } from '@/lib/money'
import { useBids } from '@/lib/queries'
import { timeAgo, useServerNow } from '@/lib/time'
import type { Auction } from '@/lib/types'

/** Live bid history. Bidders are shown only by their per-auction alias; you see yourself as "You". */
export function BidHistory({ auction }: { auction: Auction }) {
  const bids = useBids(auction.id)
  const now = useServerNow()
  const items = bids.data?.pages.flatMap((p) => p.items) ?? []
  const me = auction.viewer?.alias

  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-bold">Bid history</h2>
        <span className="text-sm text-muted-foreground tabular">{auction.bid_count} total</span>
      </div>

      {bids.isLoading ? (
        <div className="space-y-2" aria-hidden>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState title="No bids yet">Be the first to bid and set the pace.</EmptyState>
      ) : (
        <ol className="divide-y divide-border">
          {items.map((b, i) => (
            <li
              key={b.id}
              className={cn('flex items-center justify-between gap-3 py-2.5', i === 0 && 'font-semibold')}
            >
              <span className="flex items-center gap-2 text-sm">
                {i === 0 && <Crown className="size-4 text-primary" aria-label="Highest bid" />}
                <span className={cn(b.bidder_alias === me && 'text-primary')}>
                  {b.bidder_alias === me ? 'You' : b.bidder_alias}
                </span>
                {b.bidder_alias === me && <Badge tone="primary">you</Badge>}
              </span>
              <span className="flex items-baseline gap-3">
                <span className="text-xs font-normal text-muted-foreground">{timeAgo(b.created_at, now)}</span>
                <span className="tabular">{formatINR(b.amount)}</span>
              </span>
            </li>
          ))}
        </ol>
      )}

      {bids.hasNextPage && (
        <Button variant="ghost" size="sm" className="mt-2 w-full" loading={bids.isFetchingNextPage} onClick={() => void bids.fetchNextPage()}>
          Show older bids
        </Button>
      )}
    </Card>
  )
}
