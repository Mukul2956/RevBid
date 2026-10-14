import { Plus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Countdown, Price, StatusBadge } from '@/components/auction-bits'
import { ButtonLink } from '@/components/ui/button'
import { Card, ErrorState, Skeleton, Stat } from '@/components/ui/misc'
import { formatINRCompact } from '@/lib/money'
import { useAdminAuctions, useAdminStats } from '@/lib/queries'

export default function DashboardPage() {
  const stats = useAdminStats()
  const live = useAdminAuctions('LIVE')
  const liveRows = live.data?.pages.flatMap((p) => p.items) ?? []
  const s = stats.data

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">What&rsquo;s happening on the platform right now.</p>
        </div>
        <ButtonLink to="/admin/auctions/new">
            <Plus className="size-4" aria-hidden /> New auction
          </ButtonLink>
      </div>

      {stats.isError ? (
        <ErrorState message="Couldn't load platform stats." onRetry={() => void stats.refetch()} />
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {s ? (
            <>
              <Stat label="Live now" value={s.auctions_by_status.LIVE} hint={`${s.auctions_by_status.SCHEDULED} upcoming`} />
              <Stat label="Bids (last hour)" value={s.bids_last_hour} hint={`${s.total_bids} all-time`} />
              <Stat label="Sold" value={s.sold_count} hint={`${s.unsold_count} unsold`} />
              <Stat label="Sales value" value={formatINRCompact(s.gross_sold_value)} hint={`${s.users} registered users`} />
            </>
          ) : (
            Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-24 rounded-2xl" />)
          )}
        </div>
      )}

      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="font-bold">Live auctions</h2>
          <Link to="/admin/auctions" className="text-sm font-semibold text-primary underline">
            Manage all
          </Link>
        </div>
        {live.isLoading ? (
          <Skeleton className="m-4 h-24" />
        ) : liveRows.length === 0 ? (
          <p className="p-6 text-center text-sm text-muted-foreground">No auctions are live.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 font-medium">Bike</th>
                  <th className="px-4 py-2 font-medium">Current bid</th>
                  <th className="px-4 py-2 font-medium">Bids</th>
                  <th className="px-4 py-2 font-medium">Ends in</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {liveRows.map((a) => (
                  <tr key={a.id} className="hover:bg-muted/40">
                    <td className="px-4 py-3">
                      <Link to={`/admin/auctions/${a.id}`} className="font-semibold hover:underline">
                        {a.bike.year} {a.bike.make} {a.bike.model}
                      </Link>
                    </td>
                    <td className="px-4 py-3"><Price paise={a.current_price} /></td>
                    <td className="px-4 py-3 tabular">{a.bid_count}</td>
                    <td className="px-4 py-3"><Countdown auction={a} /></td>
                    <td className="px-4 py-3"><StatusBadge auction={a} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
