import { ArrowLeft, ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { BidHistory } from '@/components/BidHistory'
import { BikeImage } from '@/components/BikeImage'
import { Countdown, Price, StatusBadge } from '@/components/auction-bits'
import { Button, ButtonLink } from '@/components/ui/button'
import { Modal } from '@/components/ui/dialog'
import { Field, Input } from '@/components/ui/field'
import { Alert, Card, ErrorState, Skeleton, Stat } from '@/components/ui/misc'
import { ApiError } from '@/lib/api'
import { formatINR } from '@/lib/money'
import { useAdminAuction, useAuction, useAuctionAction, useAuditTrail } from '@/lib/queries'
import { useAuctionLive } from '@/lib/realtime'
import { formatDateTime } from '@/lib/time'

export default function AdminAuctionDetailPage() {
  const { id } = useParams()
  const admin = useAdminAuction(id)
  const live = useAuction(id) // public view, kept live by the WebSocket
  useAuctionLive(id)
  const audit = useAuditTrail(id)
  const action = useAuctionAction(id ?? '')
  const [cancelling, setCancelling] = useState(false)
  const [reason, setReason] = useState('')

  if (admin.isError) return <ErrorState message="Couldn't load this auction." onRetry={() => void admin.refetch()} />
  if (!admin.data) return <Skeleton className="h-96 rounded-2xl" />

  // Prefer the live public snapshot for fast-changing numbers; admin-only fields come from `admin`.
  const a = admin.data
  const view = live.data && live.data.version >= a.version ? { ...a, ...live.data, viewer: null } : a
  const canCancel = ['DRAFT', 'SCHEDULED', 'LIVE'].includes(view.status)

  function confirmCancel() {
    action.mutate(
      { type: 'cancel', reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success('Auction cancelled')
          setCancelling(false)
        },
        onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not cancel'),
      },
    )
  }

  return (
    <div className="space-y-5">
      <Link to="/admin/auctions" className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> All auctions
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-4">
          <BikeImage bike={view.bike} className="aspect-[16/10] w-28 shrink-0 rounded-xl" />
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-extrabold tracking-tight">
                {view.bike.year} {view.bike.make} {view.bike.model}
              </h1>
              <StatusBadge auction={view} />
            </div>
            <p className="text-sm text-muted-foreground">
              {formatDateTime(a.start_at)} → {formatDateTime(view.end_at)}
              {view.end_at !== a.original_end_at && ' (extended)'}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {view.status !== 'DRAFT' && (
            <ButtonLink to={`/auctions/${a.id}`} target="_blank" variant="secondary">
                <ExternalLink className="size-4" aria-hidden /> Public page
              </ButtonLink>
          )}
          {view.status === 'DRAFT' && (
            <Button
              loading={action.isPending}
              onClick={() =>
                action.mutate(
                  { type: 'schedule' },
                  {
                    onSuccess: () => toast.success('Auction scheduled'),
                    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not schedule'),
                  },
                )
              }
            >
              Schedule
            </Button>
          )}
          {canCancel && (
            <Button variant="danger" onClick={() => setCancelling(true)}>
              Cancel auction
            </Button>
          )}
        </div>
      </div>

      {view.status === 'CANCELLED' && <Alert tone="warning" title="Cancelled">{view.cancel_reason}</Alert>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Current bid" value={<Price paise={view.current_price} />} hint={`${view.bid_count} bids`} />
        <Stat label="Reserve" value={a.reserve_price ? formatINR(a.reserve_price) : 'None'} hint={a.reserve_price ? (view.reserve_met ? 'Met' : 'Not met') : undefined} />
        <Stat label="Increment" value={formatINR(a.min_increment)} hint={`starts ${formatINR(a.starting_price)}`} />
        <Stat label={view.status === 'LIVE' ? 'Ends in' : 'Status'} value={view.status === 'LIVE' ? <Countdown auction={view} /> : view.status} hint={`anti-snipe ${a.snipe_window_s}s / +${a.snipe_extension_s}s`} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <BidHistory auction={view} />
        <Card className="p-5">
          <h2 className="mb-3 text-lg font-bold">Audit trail</h2>
          {audit.isLoading ? (
            <Skeleton className="h-32" />
          ) : (
            <ol className="max-h-96 space-y-2 overflow-y-auto text-sm">
              {audit.data?.map((e) => (
                <li key={e.id} className="flex items-start justify-between gap-3 border-b border-border pb-2 last:border-0">
                  <span>
                    <span className="font-semibold">{e.type.replace('_', ' ')}</span>
                    {e.type === 'bid_placed' && typeof e.payload.amount === 'number' && (
                      <span className="text-muted-foreground"> · {formatINR(e.payload.amount)}</span>
                    )}
                    {e.type === 'ended' && <span className="text-muted-foreground"> · {String(e.payload.outcome)}</span>}
                    {e.type === 'cancelled' && <span className="text-muted-foreground"> · {String(e.payload.reason)}</span>}
                  </span>
                  <time className="shrink-0 text-xs text-muted-foreground" dateTime={e.created_at}>
                    {formatDateTime(e.created_at)}
                  </time>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      <Modal
        open={cancelling}
        onOpenChange={setCancelling}
        title="Cancel this auction?"
        description="Bidding stops immediately and bidders see the reason. This cannot be undone."
        footer={
          <>
            <Button variant="secondary" onClick={() => setCancelling(false)}>
              Keep running
            </Button>
            <Button variant="danger" loading={action.isPending} disabled={reason.trim().length < 3} onClick={confirmCancel}>
              Cancel auction
            </Button>
          </>
        }
      >
        <Field label="Reason (shown to bidders)">
          {(p) => <Input {...p} value={reason} maxLength={255} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Seller withdrew the bike" />}
        </Field>
      </Modal>
    </div>
  )
}
