import { Bike, Search, SlidersHorizontal, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AuctionCard, AuctionCardSkeleton } from '@/components/AuctionCard'
import { Button } from '@/components/ui/button'
import { Input, Select } from '@/components/ui/field'
import { EmptyState, ErrorState, Segmented } from '@/components/ui/misc'
import { useDebounced, useDocumentTitle } from '@/lib/hooks'
import { rupeesToPaise } from '@/lib/money'
import { useAuctions, useMakes, type AuctionFilters, type PublicStatus, type SortKey } from '@/lib/queries'
import { useLobbyLive } from '@/lib/realtime'

const TABS: { value: PublicStatus; label: string; param: string }[] = [
  { value: 'LIVE', label: 'Live now', param: 'live' },
  { value: 'SCHEDULED', label: 'Upcoming', param: 'upcoming' },
  { value: 'ENDED', label: 'Ended', param: 'ended' },
]

const SORTS: { value: SortKey; label: string }[] = [
  { value: 'ending_soon', label: 'Ending soonest' },
  { value: 'newest', label: 'Newest' },
  { value: 'price_low', label: 'Price: low to high' },
  { value: 'price_high', label: 'Price: high to low' },
]

export default function AuctionsPage() {
  useDocumentTitle('Live bike auctions')
  useLobbyLive()

  // Filters live in the URL: shareable, survive refresh, and the back button works.
  const [params, setParams] = useSearchParams()
  const status = TABS.find((t) => t.param === params.get('tab'))?.value ?? 'LIVE'
  const sort = (params.get('sort') as SortKey | null) ?? (status === 'ENDED' ? 'newest' : 'ending_soon')
  const make = params.get('make') ?? ''
  const urlQuery = params.get('q') ?? ''
  const minPrice = params.get('min') ?? ''
  const maxPrice = params.get('max') ?? ''

  const [search, setSearch] = useState(urlQuery)
  const debounced = useDebounced(search)
  const [showMore, setShowMore] = useState(Boolean(minPrice || maxPrice))

  function patch(next: Record<string, string>) {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        for (const [k, v] of Object.entries(next)) {
          if (v) p.set(k, v)
          else p.delete(k)
        }
        return p
      },
      { replace: true },
    )
  }

  useEffect(() => {
    if (debounced !== urlQuery) patch({ q: debounced })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to the debounced text
  }, [debounced])

  const filters = useMemo<AuctionFilters>(
    () => ({
      status,
      sort,
      q: urlQuery || undefined,
      make: make || undefined,
      price_min: rupeesToPaise(minPrice) ?? undefined,
      price_max: rupeesToPaise(maxPrice) ?? undefined,
    }),
    [status, sort, urlQuery, make, minPrice, maxPrice],
  )

  const makes = useMakes()
  const auctions = useAuctions(filters)
  const items = auctions.data?.pages.flatMap((p) => p.items) ?? []
  const filtered = Boolean(urlQuery || make || minPrice || maxPrice)

  return (
    <div className="space-y-6">
      <section className="space-y-1">
        <h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl">Bid on inspected bikes, live.</h1>
        <p className="max-w-2xl text-muted-foreground">
          Every bike carries a multi-point inspection score. Prices update the moment someone bids. No refresh needed.
        </p>
      </section>

      <div className="sticky top-[calc(4rem+1px)] z-30 -mx-4 space-y-3 border-b border-border bg-background/90 px-4 py-3 backdrop-blur sm:top-16 sm:-mx-6 sm:px-6">
        <div className="flex flex-wrap items-center gap-3">
          <Segmented
            label="Auction status"
            value={status}
            onChange={(v) => patch({ tab: TABS.find((t) => t.value === v)!.param, sort: '' })}
            options={TABS.map((t) => ({ value: t.value, label: t.label }))}
          />
          <div className="relative min-w-52 flex-1 sm:max-w-sm">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input
              type="search"
              aria-label="Search by make or model"
              placeholder="Search make or model…"
              className="pl-9"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <Select aria-label="Make" className="w-auto min-w-36" value={make} onChange={(e) => patch({ make: e.target.value })}>
            <option value="">All makes</option>
            {makes.data?.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </Select>
          <Select aria-label="Sort" className="w-auto min-w-44" value={sort} onChange={(e) => patch({ sort: e.target.value })}>
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </Select>
          <Button variant="secondary" size="md" onClick={() => setShowMore((v) => !v)} aria-expanded={showMore}>
            <SlidersHorizontal className="size-4" aria-hidden /> Price
          </Button>
          {filtered && (
            <Button
              variant="ghost"
              onClick={() => {
                setSearch('')
                patch({ q: '', make: '', min: '', max: '' })
              }}
            >
              <X className="size-4" aria-hidden /> Clear
            </Button>
          )}
        </div>
        {showMore && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-muted-foreground">Price (₹)</span>
            <Input inputMode="numeric" aria-label="Minimum price in rupees" placeholder="Min" className="w-36" value={minPrice} onChange={(e) => patch({ min: e.target.value.replace(/\D/g, '') })} />
            <span aria-hidden>to</span>
            <Input inputMode="numeric" aria-label="Maximum price in rupees" placeholder="Max" className="w-36" value={maxPrice} onChange={(e) => patch({ max: e.target.value.replace(/\D/g, '') })} />
          </div>
        )}
      </div>

      {auctions.isError ? (
        <ErrorState message="We couldn't load auctions right now." onRetry={() => void auctions.refetch()} />
      ) : auctions.isLoading ? (
        <Grid>
          {Array.from({ length: 8 }, (_, i) => (
            <AuctionCardSkeleton key={i} />
          ))}
        </Grid>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Bike className="size-10" />}
          title={filtered ? 'No bikes match your filters' : `No ${TABS.find((t) => t.value === status)!.label.toLowerCase()} auctions`}
        >
          {filtered
            ? 'Try removing a filter or searching for a different make.'
            : status === 'LIVE'
              ? 'Nothing is live at the moment. Check the upcoming tab for what’s next.'
              : 'Check back soon.'}
        </EmptyState>
      ) : (
        <>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {items.length}
            {auctions.hasNextPage ? '+' : ''} {items.length === 1 ? 'auction' : 'auctions'}
          </p>
          <Grid>
            {items.map((a) => (
              <AuctionCard key={a.id} auction={a} />
            ))}
          </Grid>
          {auctions.hasNextPage && (
            <div className="flex justify-center">
              <Button variant="secondary" loading={auctions.isFetchingNextPage} onClick={() => void auctions.fetchNextPage()}>
                Load more
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Grid({ children }: { children: React.ReactNode }) {
  return <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{children}</div>
}
