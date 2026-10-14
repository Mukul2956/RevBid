import { Plus } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { StatusBadge } from '@/components/auction-bits'
import { Button, ButtonLink } from '@/components/ui/button'
import { Card, EmptyState, ErrorState, Segmented, Skeleton } from '@/components/ui/misc'
import { ApiError } from '@/lib/api'
import { formatINR } from '@/lib/money'
import { formatDateTime } from '@/lib/time'
import { useAdminAuctions, useAuctionAction } from '@/lib/queries'
import type { AdminAuction, AuctionStatus } from '@/lib/types'

type Filter = AuctionStatus | 'ALL'
const FILTERS: { value: Filter; label: string }[] = [
  { value: 'ALL', label: 'All' },
  { value: 'DRAFT', label: 'Drafts' },
  { value: 'SCHEDULED', label: 'Scheduled' },
  { value: 'LIVE', label: 'Live' },
  { value: 'ENDED', label: 'Ended' },
  { value: 'CANCELLED', label: 'Cancelled' },
]

export default function AdminAuctionsPage() {
  const [filter, setFilter] = useState<Filter>('ALL')
  const query = useAdminAuctions(filter)
  const rows = query.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-extrabold tracking-tight">Auctions</h1>
        <ButtonLink to="/admin/auctions/new">
            <Plus className="size-4" aria-hidden /> New auction
          </ButtonLink>
      </div>

      <div className="overflow-x-auto">
        <Segmented label="Filter by status" value={filter} onChange={setFilter} options={FILTERS} />
      </div>

      {query.isError ? (
        <ErrorState message="Couldn't load auctions." onRetry={() => void query.refetch()} />
      ) : query.isLoading ? (
        <Skeleton className="h-64 rounded-2xl" />
      ) : rows.length === 0 ? (
        <EmptyState title="No auctions here">Create one to get started.</EmptyState>
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 font-medium">Bike</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Price</th>
                  <th className="px-4 py-2 font-medium">Bids</th>
                  <th className="px-4 py-2 font-medium">Window</th>
                  <th className="px-4 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((a) => (
                  <Row key={a.id} auction={a} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {query.hasNextPage && (
        <div className="flex justify-center">
          <Button variant="secondary" loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
            Load more
          </Button>
        </div>
      )}
    </div>
  )
}

function Row({ auction: a }: { auction: AdminAuction }) {
  const action = useAuctionAction(a.id)
  return (
    <tr className="hover:bg-muted/40">
      <td className="px-4 py-3">
        <Link to={`/admin/auctions/${a.id}`} className="font-semibold hover:underline">
          {a.bike.year} {a.bike.make} {a.bike.model}
        </Link>
      </td>
      <td className="px-4 py-3"><StatusBadge auction={a} /></td>
      <td className="px-4 py-3 tabular">
        {formatINR(a.current_price)}
        {a.reserve_price && <span className="block text-xs text-muted-foreground">reserve {formatINR(a.reserve_price)}</span>}
      </td>
      <td className="px-4 py-3 tabular">{a.bid_count}</td>
      <td className="px-4 py-3 text-xs text-muted-foreground">
        {formatDateTime(a.start_at)}
        <br />
        {formatDateTime(a.end_at)}
      </td>
      <td className="px-4 py-3 text-right">
        <div className="flex justify-end gap-2">
          {a.status === 'DRAFT' && (
            <Button
              size="sm"
              loading={action.isPending}
              onClick={() =>
                action.mutate(
                  { type: 'schedule' },
                  {
                    onSuccess: () => toast.success('Auction scheduled'),
                    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Failed to schedule'),
                  },
                )
              }
            >
              Schedule
            </Button>
          )}
          <ButtonLink to={`/admin/auctions/${a.id}`} size="sm" variant="secondary">
              Open
            </ButtonLink>
        </div>
      </td>
    </tr>
  )
}
