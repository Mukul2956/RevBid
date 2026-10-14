import { Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'
import { BikeImage } from '@/components/BikeImage'
import { Button } from '@/components/ui/button'
import { Modal } from '@/components/ui/dialog'
import { Field, Input, Select, Textarea } from '@/components/ui/field'
import { Alert, Card, EmptyState, ErrorState, Skeleton } from '@/components/ui/misc'
import { ApiError, fieldErrors } from '@/lib/api'
import { useDebounced } from '@/lib/hooks'
import { formatKm } from '@/lib/money'
import { useAdminBikes, useDeleteBike, useSaveBike } from '@/lib/queries'
import type { Bike, BikeInput, Condition } from '@/lib/types'

const blank: BikeInput = {
  make: '',
  model: '',
  variant: null,
  year: new Date().getFullYear() - 2,
  km_driven: 0,
  owner_number: 1,
  engine_cc: null,
  color: null,
  registration_state: null,
  condition: 'GOOD',
  inspection_score: null,
  description: '',
  images: [],
}

export default function BikesPage() {
  const [search, setSearch] = useState('')
  const q = useDebounced(search)
  const bikes = useAdminBikes(q)
  const del = useDeleteBike()
  const [editing, setEditing] = useState<Bike | 'new' | null>(null)
  const rows = bikes.data?.pages.flatMap((p) => p.items) ?? []

  function remove(b: Bike) {
    if (!window.confirm(`Delete ${b.year} ${b.make} ${b.model}? This cannot be undone.`)) return
    del.mutate(b.id, {
      onSuccess: () => toast.success('Bike deleted'),
      onError: (e) =>
        toast.error(e instanceof ApiError && e.status === 409 ? 'This bike has auctions and cannot be deleted.' : 'Delete failed'),
    })
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-extrabold tracking-tight">Bikes</h1>
        <Button onClick={() => setEditing('new')}>
          <Plus className="size-4" aria-hidden /> Add bike
        </Button>
      </div>

      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input type="search" aria-label="Search bikes" placeholder="Search make or model…" className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      {bikes.isError ? (
        <ErrorState message="Couldn't load bikes." onRetry={() => void bikes.refetch()} />
      ) : bikes.isLoading ? (
        <Skeleton className="h-64 rounded-2xl" />
      ) : rows.length === 0 ? (
        <EmptyState title="No bikes found">{q ? 'Try a different search.' : 'Add your first bike to list it for auction.'}</EmptyState>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {rows.map((b) => (
              <li key={b.id} className="flex items-center gap-4 p-3">
                <BikeImage bike={b} className="aspect-[16/10] w-24 shrink-0 rounded-lg" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold">
                    {b.year} {b.make} {b.model} {b.variant}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {formatKm(b.km_driven)} · owner {b.owner_number} · {b.condition.toLowerCase()}
                    {b.inspection_score !== null && ` · score ${b.inspection_score}`}
                  </p>
                </div>
                <div className="flex gap-1">
                  <Button variant="ghost" size="sm" onClick={() => setEditing(b)} aria-label={`Edit ${b.make} ${b.model}`}>
                    <Pencil className="size-4" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => remove(b)} aria-label={`Delete ${b.make} ${b.model}`}>
                    <Trash2 className="size-4 text-danger" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {bikes.hasNextPage && (
        <div className="flex justify-center">
          <Button variant="secondary" loading={bikes.isFetchingNextPage} onClick={() => void bikes.fetchNextPage()}>
            Load more
          </Button>
        </div>
      )}

      {editing && <BikeDialog bike={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

function BikeDialog({ bike, onClose }: { bike: Bike | null; onClose: () => void }) {
  const save = useSaveBike(bike?.id)
  const [f, setF] = useState<BikeInput>(bike ? { ...bike } : blank)
  const [imagesText, setImagesText] = useState((bike?.images ?? []).join('\n'))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const set = <K extends keyof BikeInput>(k: K, v: BikeInput[K]) => setF((s) => ({ ...s, [k]: v }))
  const num = (s: string) => (s === '' ? null : Number(s))

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const { id: _id, created_at: _c, ...rest } = f as BikeInput & { id?: string; created_at?: string }
    void _id
    void _c
    save.mutate(
      { ...rest, images: imagesText.split('\n').map((s) => s.trim()).filter(Boolean) },
      {
        onSuccess: () => {
          toast.success(bike ? 'Bike updated' : 'Bike added')
          onClose()
        },
        onError: (err) => {
          setErrors(fieldErrors(err))
          setFormError(err instanceof ApiError ? err.message : 'Could not save')
        },
      },
    )
  }

  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={bike ? 'Edit bike' : 'Add bike'} className="sm:max-w-2xl">
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {formError && <Alert tone="danger">{formError}</Alert>}
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Make" error={errors.make}>{(p) => <Input {...p} value={f.make} onChange={(e) => set('make', e.target.value)} />}</Field>
          <Field label="Model" error={errors.model}>{(p) => <Input {...p} value={f.model} onChange={(e) => set('model', e.target.value)} />}</Field>
          <Field label="Variant" error={errors.variant}>{(p) => <Input {...p} value={f.variant ?? ''} onChange={(e) => set('variant', e.target.value || null)} />}</Field>
          <Field label="Year" error={errors.year}>{(p) => <Input {...p} type="number" value={f.year} onChange={(e) => set('year', Number(e.target.value))} />}</Field>
          <Field label="Odometer (km)" error={errors.km_driven}>{(p) => <Input {...p} type="number" min={0} value={f.km_driven} onChange={(e) => set('km_driven', Number(e.target.value))} />}</Field>
          <Field label="Owner #" error={errors.owner_number}>{(p) => <Input {...p} type="number" min={1} value={f.owner_number} onChange={(e) => set('owner_number', Number(e.target.value))} />}</Field>
          <Field label="Engine (cc)" error={errors.engine_cc}>{(p) => <Input {...p} type="number" value={f.engine_cc ?? ''} onChange={(e) => set('engine_cc', num(e.target.value))} />}</Field>
          <Field label="Colour" error={errors.color}>{(p) => <Input {...p} value={f.color ?? ''} onChange={(e) => set('color', e.target.value || null)} />}</Field>
          <Field label="Registered in" error={errors.registration_state}>{(p) => <Input {...p} value={f.registration_state ?? ''} onChange={(e) => set('registration_state', e.target.value || null)} />}</Field>
          <Field label="Condition" error={errors.condition}>
            {(p) => (
              <Select {...p} value={f.condition} onChange={(e) => set('condition', e.target.value as Condition)}>
                <option value="EXCELLENT">Excellent</option>
                <option value="GOOD">Good</option>
                <option value="FAIR">Fair</option>
              </Select>
            )}
          </Field>
          <Field label="Inspection score (0-100)" error={errors.inspection_score}>{(p) => <Input {...p} type="number" min={0} max={100} value={f.inspection_score ?? ''} onChange={(e) => set('inspection_score', num(e.target.value))} />}</Field>
        </div>
        <Field label="Seller notes" error={errors.description}>{(p) => <Textarea {...p} value={f.description} onChange={(e) => set('description', e.target.value)} />}</Field>
        <Field label="Image URLs (one per line, https only)" hint="Leave empty to show an illustration." error={errors.images}>
          {(p) => <Textarea {...p} className="font-mono text-xs" value={imagesText} onChange={(e) => setImagesText(e.target.value)} placeholder="https://…/photo.jpg" />}
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={save.isPending}>
            {bike ? 'Save changes' : 'Add bike'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
