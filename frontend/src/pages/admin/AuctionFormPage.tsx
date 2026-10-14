import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Field, Input, Select } from '@/components/ui/field'
import { Alert, Card } from '@/components/ui/misc'
import { api, ApiError, fieldErrors } from '@/lib/api'
import { formatINR, formatRupees, rupeesToPaise } from '@/lib/money'
import { useAdminBikes, useCreateAuction } from '@/lib/queries'
import { serverNow, syncServerTime, toLocalInputValue } from '@/lib/time'

const DURATIONS = [
  { label: '5 min', minutes: 5 },
  { label: '15 min', minutes: 15 },
  { label: '1 hour', minutes: 60 },
  { label: '1 day', minutes: 1440 },
  { label: '3 days', minutes: 4320 },
]

/** Round 1% of the starting price to a sensible rupee step (₹100 / ₹500 / ₹1,000). */
function suggestIncrement(startingPaise: number): number {
  const onePct = startingPaise / 100 / 100 // rupees
  const step = onePct >= 1000 ? 1000 : onePct >= 400 ? 500 : 100
  return Math.max(step, Math.round(onePct / step) * step) * 100
}

export default function AuctionFormPage() {
  const navigate = useNavigate()
  const create = useCreateAuction()
  const bikes = useAdminBikes('', 100)

  const [bikeId, setBikeId] = useState('')
  const [startMode, setStartMode] = useState<'now' | 'later'>('now')
  const [startLocal, setStartLocal] = useState(() => toLocalInputValue(new Date(Date.now() + 3600_000)))
  const [duration, setDuration] = useState(15)
  const [starting, setStarting] = useState('')
  const [reserve, setReserve] = useState('')
  const [increment, setIncrement] = useState('')
  const [incrementTouched, setIncrementTouched] = useState(false)
  const [snipe, setSnipe] = useState({ window: '120', extension: '120', cap: '1800' })
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  // The browser clock can be wrong; "start now" must use the server's idea of now.
  useEffect(() => {
    void api<{ server_time: string }>('/time', { auth: false }).then((t) => syncServerTime(t.server_time))
  }, [])

  const startingPaise = rupeesToPaise(starting)
  useEffect(() => {
    if (!incrementTouched && startingPaise) setIncrement(formatRupees(suggestIncrement(startingPaise)))
  }, [startingPaise, incrementTouched])

  const bikeList = bikes.data?.pages.flatMap((p) => p.items) ?? []
  function submit(schedule: boolean) {
    const next: Record<string, string> = {}
    const startingP = rupeesToPaise(starting)
    const incrementP = rupeesToPaise(increment)
    const reserveP = reserve.trim() ? rupeesToPaise(reserve) : null
    if (!bikeId) next.bike_id = 'Choose a bike'
    if (!startingP) next.starting_price = 'Enter a whole-rupee starting price'
    if (!incrementP) next.min_increment = 'Enter a whole-rupee increment'
    if (reserve.trim() && !reserveP) next.reserve_price = 'Enter a whole-rupee reserve (or leave empty)'
    if (startingP && reserveP && reserveP < startingP) next.reserve_price = 'Reserve cannot be below the starting price'
    if (duration < 1) next.duration = 'Duration must be at least 1 minute'
    setErrors(next)
    setFormError(null)
    if (Object.keys(next).length || !startingP || !incrementP) return

    const start = startMode === 'now' ? new Date(serverNow()) : new Date(startLocal)
    const end = new Date(start.getTime() + duration * 60_000)
    create.mutate(
      {
        bike_id: bikeId,
        start_at: start.toISOString(),
        end_at: end.toISOString(),
        starting_price: startingP,
        reserve_price: reserveP,
        min_increment: incrementP,
        snipe_window_s: Number(snipe.window),
        snipe_extension_s: Number(snipe.extension),
        max_extension_s: Number(snipe.cap),
        schedule,
      },
      {
        onSuccess: (a) => {
          toast.success(schedule ? 'Auction scheduled' : 'Draft saved')
          navigate(`/admin/auctions/${a.id}`)
        },
        onError: (err) => {
          if (err instanceof ApiError && err.code === 'VALIDATION_ERROR') setErrors(fieldErrors(err))
          setFormError(err instanceof ApiError ? err.message : 'Could not create the auction')
        },
      },
    )
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    submit(true)
  }

  return (
    <div className="max-w-2xl space-y-5">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight">New auction</h1>
        <p className="text-sm text-muted-foreground">
          Pick a bike, set the pricing, choose when it runs.{' '}
          <Link to="/admin/bikes" className="font-semibold text-primary underline">
            Need to add a bike first?
          </Link>
        </p>
      </div>

      <form onSubmit={onSubmit} noValidate className="space-y-5">
        {formError && <Alert tone="danger">{formError}</Alert>}

        <Card className="space-y-4 p-5">
          <h2 className="font-bold">Bike</h2>
          <Field label="Bike" error={errors.bike_id}>
            {(p) => (
              <Select {...p} value={bikeId} onChange={(e) => setBikeId(e.target.value)}>
                <option value="">Select a bike…</option>
                {bikeList.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.year} {b.make} {b.model}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </Card>

        <Card className="space-y-4 p-5">
          <h2 className="font-bold">Pricing (₹)</h2>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Starting price" error={errors.starting_price}>
              {(p) => <Input {...p} inputMode="numeric" placeholder="85,000" value={starting} onChange={(e) => setStarting(e.target.value.replace(/[^\d,]/g, ''))} />}
            </Field>
            <Field label="Reserve (optional)" hint="Hidden from bidders" error={errors.reserve_price}>
              {(p) => <Input {...p} inputMode="numeric" placeholder="None" value={reserve} onChange={(e) => setReserve(e.target.value.replace(/[^\d,]/g, ''))} />}
            </Field>
            <Field label="Min. increment" error={errors.min_increment}>
              {(p) => (
                <Input {...p} inputMode="numeric" value={increment} onChange={(e) => { setIncrementTouched(true); setIncrement(e.target.value.replace(/[^\d,]/g, '')) }} />
              )}
            </Field>
          </div>
          {startingPaise && (
            <p className="text-xs text-muted-foreground">
              Bidding opens at {formatINR(startingPaise)}; each new bid must beat the lead by {increment ? `₹${increment}` : 'the increment'}.
            </p>
          )}
        </Card>

        <Card className="space-y-4 p-5">
          <h2 className="font-bold">Schedule</h2>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Start time">
            {(['now', 'later'] as const).map((m) => (
              <Button key={m} variant={startMode === m ? 'primary' : 'secondary'} size="sm" onClick={() => setStartMode(m)} aria-pressed={startMode === m}>
                {m === 'now' ? 'Start now' : 'Schedule for later'}
              </Button>
            ))}
          </div>
          {startMode === 'later' && (
            <Field label="Start time (your local time)" error={errors.start_at}>
              {(p) => <Input {...p} type="datetime-local" value={startLocal} onChange={(e) => setStartLocal(e.target.value)} />}
            </Field>
          )}
          <Field label="Duration" error={errors.duration ?? errors.end_at}>
            {(p) => (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-2" role="group" aria-label="Duration presets">
                  {DURATIONS.map((d) => (
                    <Button key={d.minutes} size="sm" variant={duration === d.minutes ? 'primary' : 'secondary'} onClick={() => setDuration(d.minutes)} aria-pressed={duration === d.minutes}>
                      {d.label}
                    </Button>
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  <Input {...p} type="number" min={1} className="w-32" value={duration} onChange={(e) => setDuration(Number(e.target.value))} />
                  <span className="text-sm text-muted-foreground">minutes</span>
                </div>
              </div>
            )}
          </Field>
        </Card>

        <details className="rounded-2xl border border-border bg-card p-5">
          <summary className="cursor-pointer font-bold">Anti-sniping (advanced)</summary>
          <p className="mt-2 text-sm text-muted-foreground">
            A bid in the final <strong>window</strong> pushes the end time out by <strong>extension</strong>, up to <strong>cap</strong> seconds in total, so nobody can win with a last-second bid nobody could answer.
          </p>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            {([['window', 'Window (s)'], ['extension', 'Extension (s)'], ['cap', 'Total cap (s)']] as const).map(([key, label]) => (
              <Field key={key} label={label}>
                {(p) => <Input {...p} type="number" min={0} value={snipe[key]} onChange={(e) => setSnipe((s) => ({ ...s, [key]: e.target.value }))} />}
              </Field>
            ))}
          </div>
        </details>

        <div className="flex flex-wrap gap-3">
          <Button type="submit" size="lg" loading={create.isPending}>
            Create &amp; schedule
          </Button>
          <Button variant="secondary" size="lg" disabled={create.isPending} onClick={() => submit(false)}>
            Save as draft
          </Button>
        </div>
      </form>
    </div>
  )
}
