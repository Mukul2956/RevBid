import { Calendar, Gauge, MapPin, Palette, ShieldCheck, Users, Zap } from 'lucide-react'
import type { ReactNode } from 'react'
import { InspectionScore } from '@/components/auction-bits'
import { Card } from '@/components/ui/misc'
import { formatKm, ordinal } from '@/lib/money'
import type { Bike } from '@/lib/types'

function Spec({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
  return (
    <div className="flex items-center gap-3 rounded-xl bg-muted/60 p-3">
      <div className="text-primary" aria-hidden>
        {icon}
      </div>
      <div className="min-w-0">
        <dt className="text-xs text-muted-foreground">{label}</dt>
        <dd className="truncate text-sm font-semibold">{value}</dd>
      </div>
    </div>
  )
}

export function BikeSpecs({ bike }: { bike: Bike }) {
  return (
    <Card className="space-y-5 p-5">
      <h2 className="text-lg font-bold">Specifications</h2>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Spec icon={<Calendar className="size-5" />} label="Year" value={bike.year} />
        <Spec icon={<Gauge className="size-5" />} label="Odometer" value={formatKm(bike.km_driven)} />
        <Spec icon={<Users className="size-5" />} label="Ownership" value={ordinal(bike.owner_number)} />
        {bike.engine_cc && <Spec icon={<Zap className="size-5" />} label="Engine" value={`${bike.engine_cc} cc`} />}
        {bike.registration_state && (
          <Spec icon={<MapPin className="size-5" />} label="Registered in" value={bike.registration_state} />
        )}
        {bike.color && <Spec icon={<Palette className="size-5" />} label="Colour" value={bike.color} />}
        <Spec icon={<ShieldCheck className="size-5" />} label="Condition" value={bike.condition.charAt(0) + bike.condition.slice(1).toLowerCase()} />
      </dl>
      {bike.inspection_score !== null && <InspectionScore score={bike.inspection_score} />}
      {bike.description && (
        <div>
          <h3 className="mb-1 text-sm font-semibold">Seller notes</h3>
          <p className="text-sm leading-relaxed text-muted-foreground">{bike.description}</p>
        </div>
      )}
    </Card>
  )
}
