import { Gavel } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { BikeImage } from '@/components/BikeImage'
import { Countdown, Price, StatusBadge } from '@/components/auction-bits'
import { Badge, Card, EmptyState, ErrorState, Segmented, Skeleton } from '@/components/ui/misc'
import { useDocumentTitle } from '@/lib/hooks'
import { formatINR } from '@/lib/money'
import { useMyBids } from '@/lib/queries'
import { useLobbyLive } from '@/lib/realtime'
import type { MyBid, MyBidResult } from '@/lib/types'

type Tab = 'active' | 'won' | 'closed'

const groups: Record<Tab, MyBidResult[]> = {
  active: ['LEADING', 'OUTBID'],
  won: ['WON'],
  closed: ['LOST', 'CANCELLED'],
}

const resultBadge: Record<MyBidResult, { tone: 'success' | 'warning' | 'neutral' | 'primary'; label: string }> = {
  LEADING: { tone: 'success', label: "You're leading" },
  OUTBID: { tone: 'warning', label: 'Outbid' },
  WON: { tone: 'success', label: 'Won' },
  LOST: { tone: 'neutral', label: 'Lost' },
  CANCELLED: { tone: 'neutral', label: 'Cancelled' },
}

export default function MyBidsPage() {
  useDocumentTitle('My bids')
  useLobbyLive()
  const [tab, setTab] = useState<Tab>('active')
  const bids = useMyBids(true)

  const counts = useMemo(() => {
    const c: Record<Tab, number> = { active: 0, won: 0, closed: 0 }
    for (const b of bids.data ?? []) {
      for (const t of Object.keys(groups) as Tab[]) if (groups[t].includes(b.result)) c[t] += 1
    }
    return c
  }, [bids.data])

  const rows = (bids.data ?? []).filter((b) => groups[tab].includes(b.result))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight">My bids</h1>
        <p className="text-muted-foreground">Everything you&rsquo;ve bid on, and where you stand.</p>
      </div>

      <Segmented
        label="Bid status"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'active', label: `Active (${counts.active})` },
          { value: 'won', label: `Won (${counts.won})` },
          { value: 'closed', label: `Closed (${counts.closed})` },
        ]}
      />

      {bids.isError ? (
        <ErrorState message="We couldn't load your bids." onRetry={() => void bids.refetch()} />
      ) : bids.isLoading ? (
        <div className="space-y-3" aria-hidden>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28 rounded-2xl" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState icon={<Gavel className="size-10" />} title={tab === 'active' ? 'No active bids' : tab === 'won' ? 'No wins yet' : 'Nothing here'}
          action={<Link to="/" className="text-sm font-semibold text-primary underline">Browse live auctions</Link>}>
          {tab === 'active' ? 'Find a bike you like and place your first bid.' : 'Auctions you have finished will show up here.'}
        </EmptyState>
      ) : (
        <ul className="space-y-3">
          {rows.map((b) => (
            <li key={b.auction.id}>
              <BidRow bid={b} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function BidRow({ bid }: { bid: MyBid }) {
  const { auction: a } = bid
  const badge = resultBadge[bid.result]
  return (
    <Link to={`/auctions/${a.id}`} className="block rounded-2xl">
      <Card className="flex flex-col gap-4 p-3 transition-shadow hover:shadow-md sm:flex-row sm:items-center">
        <BikeImage bike={a.bike} className="aspect-[16/10] w-full shrink-0 rounded-xl sm:w-44" />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-base font-bold">
              {a.bike.year} {a.bike.make} {a.bike.model}
            </h2>
            <StatusBadge auction={a} />
          </div>
          <p className="text-sm text-muted-foreground">
            Your highest bid <span className="font-semibold text-foreground tabular">{formatINR(bid.my_highest_bid)}</span>
          </p>
          <Countdown auction={a} className="text-sm" />
        </div>
        <div className="flex items-center justify-between gap-4 sm:flex-col sm:items-end">
          <div className="text-right">
            <p className="text-xs text-muted-foreground">{a.status === 'ENDED' ? 'Final price' : 'Current bid'}</p>
            <Price paise={a.current_price} className="text-xl font-bold" />
          </div>
          <Badge tone={badge.tone}>{badge.label}</Badge>
        </div>
      </Card>
    </Link>
  )
}
