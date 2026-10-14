import { CheckCircle2, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import { Alert, Card, ErrorState, Skeleton } from '@/components/ui/misc'
import { cn } from '@/lib/cn'
import { useAdminSystem } from '@/lib/queries'
import type { ComponentHealth } from '@/lib/types'

function uptime(s: number): string {
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`
}

function Health({ name, h }: { name: string; h: ComponentHealth }) {
  return (
    <Card className="flex items-center justify-between p-4">
      <div>
        <p className="text-sm font-semibold">{name}</p>
        <p className="text-xs text-muted-foreground">{h.ok ? `${h.latency_ms} ms` : h.error}</p>
      </div>
      {h.ok ? (
        <CheckCircle2 className="size-6 text-success" aria-label="healthy" />
      ) : (
        <XCircle className="size-6 text-danger" aria-label="failing" />
      )}
    </Card>
  )
}

function Metric({ label, value, tone }: { label: string; value: ReactNode; tone?: 'ok' | 'bad' }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
      <p className={cn('mt-1 text-2xl font-bold tabular', tone === 'bad' && 'text-danger', tone === 'ok' && 'text-success')}>{value}</p>
    </Card>
  )
}

/** Live operational snapshot: the in-app counterpart to the Prometheus/Grafana dashboards. */
export default function SystemPage() {
  const sys = useAdminSystem()
  if (sys.isError) return <ErrorState message="Couldn't reach the system endpoint." onRetry={() => void sys.refetch()} />
  const s = sys.data
  if (!s) return <Skeleton className="h-64 rounded-2xl" />

  const tickAge = s.scheduler_last_tick_age_s
  const stalled = s.scheduler_enabled && (tickAge === null || tickAge > 10)

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight">System health</h1>
        <p className="text-sm text-muted-foreground">
          v{s.version} · {s.env} · up {uptime(s.uptime_s)} · refreshes every 5 s
          {s.demo_mode && ' · demo mode (auctions auto-replenish)'}
        </p>
      </div>

      {stalled && (
        <Alert tone="danger" title="Scheduler looks stalled">
          No lifecycle tick in {tickAge === null ? 'a while' : `${tickAge}s`}. Auctions may not start or end on time.
          {s.scheduler_last_error && ` Last error: ${s.scheduler_last_error}`}
        </Alert>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <Health name="PostgreSQL" h={s.database} />
        <Health name="Redis (pub/sub, rate limits)" h={s.redis} />
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="Open sockets" value={s.websocket_connections} />
        <Metric label="Active rooms" value={s.websocket_rooms} />
        <Metric label="Bids / min" value={s.bids_last_minute} />
        <Metric
          label="Scheduler tick"
          value={s.scheduler_enabled ? (tickAge === null ? 'n/a' : `${tickAge}s ago`) : 'disabled'}
          tone={s.scheduler_enabled ? (stalled ? 'bad' : 'ok') : undefined}
        />
      </div>

      <Card className="p-4 text-sm text-muted-foreground">
        Prometheus metrics are exposed at <code className="rounded bg-muted px-1.5 py-0.5">/metrics</code> (bid latency,
        lock-wait, scheduler lag, WebSocket gauges, rate-limit rejections). Structured JSON logs carry a request id
        on every line.
      </Card>
    </div>
  )
}
