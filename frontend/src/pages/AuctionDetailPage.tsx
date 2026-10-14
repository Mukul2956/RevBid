import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { BidHistory } from '@/components/BidHistory'
import { BidPanel } from '@/components/BidPanel'
import { BikeImage } from '@/components/BikeImage'
import { BikeSpecs } from '@/components/BikeSpecs'
import { Card, ErrorState, Skeleton } from '@/components/ui/misc'
import { ApiError } from '@/lib/api'
import { useDocumentTitle } from '@/lib/hooks'
import { useAuction } from '@/lib/queries'
import type { Bike } from '@/lib/types'
import { useAuctionLive } from '@/lib/realtime'
import NotFoundPage from './NotFoundPage'

export default function AuctionDetailPage() {
  const { id } = useParams()
  const auction = useAuction(id)
  useAuctionLive(id) // WebSocket events patch the cache; no polling needed in the happy path
  const bike = auction.data?.bike
  useDocumentTitle(bike ? `${bike.year} ${bike.make} ${bike.model}` : 'Auction')

  if (auction.error instanceof ApiError && auction.error.status === 404) return <NotFoundPage />
  if (auction.isError) {
    return <ErrorState message="We couldn't load this auction." onRetry={() => void auction.refetch()} />
  }
  if (!auction.data || !bike) return <DetailSkeleton />

  const a = auction.data
  return (
    <div className="space-y-5">
      <Link to="/" className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> All auctions
      </Link>

      {/*
        One BidPanel, placed by CSS grid: DOM order (title, panel, details) is the phone order, and on
        large screens the panel moves to a sticky right column. Rendering it twice (one hidden per
        breakpoint) would duplicate ids, live regions and mutation state.
      */}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_400px] lg:grid-rows-[auto_1fr]">
        <div className="lg:col-start-1">
          <h1 className="text-2xl font-extrabold tracking-tight sm:text-3xl">
            {bike.year} {bike.make} {bike.model}
          </h1>
          {bike.variant && <p className="text-muted-foreground">{bike.variant}</p>}
        </div>

        <aside className="lg:col-start-2 lg:row-span-2 lg:row-start-1">
          <div className="lg:sticky lg:top-24">
            <BidPanel auction={a} />
          </div>
        </aside>

        <div className="space-y-6 lg:col-start-1">
          <Gallery bike={bike} />
          <BikeSpecs bike={bike} />
          <BidHistory auction={a} />
        </div>
      </div>
    </div>
  )
}

function Gallery({ bike }: { bike: Bike }) {
  const extra = bike.images.slice(1, 5)
  return (
    <div className="space-y-2">
      <BikeImage key={bike.id} bike={bike} eager sizes="(min-width: 1024px) 60vw, 100vw" className="aspect-[16/10] rounded-2xl border border-border" />
      {extra.length > 0 && (
        <ul className="grid grid-cols-4 gap-2">
          {extra.map((src) => (
            <li key={src} className="aspect-[4/3] overflow-hidden rounded-xl border border-border">
              <img src={src} alt="" loading="lazy" className="size-full object-cover" />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function DetailSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_400px]" aria-hidden>
      <div className="space-y-6">
        <Skeleton className="h-9 w-2/3" />
        <Skeleton className="aspect-[16/10] rounded-2xl" />
        <Skeleton className="h-64" />
      </div>
      <Card className="h-96 p-5">
        <Skeleton className="h-full" />
      </Card>
    </div>
  )
}
