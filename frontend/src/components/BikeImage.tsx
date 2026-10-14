import { useState } from 'react'
import { cn } from '@/lib/cn'
import type { Bike } from '@/lib/types'

/** Stable hue per make, so every Royal Enfield card shares a tint. */
function hueFor(text: string): number {
  let h = 0
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) % 360
  return h
}

/** A drawn motorcycle: used when a listing has no photo (or the photo fails to load). */
function Illustration({ bike, className }: { bike: Pick<Bike, 'make' | 'model'>; className?: string }) {
  const hue = hueFor(bike.make)
  return (
    <div
      className={cn('flex size-full items-center justify-center', className)}
      style={{
        background: `linear-gradient(135deg, hsl(${hue} 70% 55% / 0.22), hsl(${(hue + 40) % 360} 70% 55% / 0.06))`,
        color: `hsl(${hue} 55% 42%)`,
      }}
      role="img"
      aria-label={`${bike.make} ${bike.model} (no photo available)`}
    >
      <svg viewBox="0 0 240 140" className="w-3/5 max-w-[320px]" fill="none" stroke="currentColor" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <circle cx="58" cy="100" r="28" />
        <circle cx="182" cy="100" r="28" />
        <circle cx="58" cy="100" r="5" fill="currentColor" />
        <circle cx="182" cy="100" r="5" fill="currentColor" />
        <path d="M58 100 L92 62 H150 L182 100" />
        <path d="M92 62 L112 100 H146" />
        <path d="M112 54 q22 -16 46 0" strokeWidth="9" />
        <path d="M78 58 h28" strokeWidth="8" />
        <path d="M150 62 L160 40 H176" />
        <path d="M124 80 h20" strokeWidth="9" />
      </svg>
    </div>
  )
}

// Wikimedia serves fixed thumbnail steps, so one stored URL can offer the browser several sizes
// (a 500px card image is ~5x lighter than the 1280px original). Other hosts get a plain src.
const COMMONS_THUMB = /^(https:\/\/[a-z.]*wikimedia\.org\/.*\/)\d+px-(.*)$/
const STEPS = [500, 960, 1280]

export function responsiveSrcSet(src: string): string | undefined {
  const m = COMMONS_THUMB.exec(src)
  return m ? STEPS.map((w) => `${m[1]}${w}px-${m[2]} ${w}w`).join(', ') : undefined
}

const CARD_SIZES = '(min-width: 1280px) 25vw, (min-width: 1024px) 33vw, (min-width: 640px) 50vw, 100vw'

export function BikeImage({
  bike,
  className,
  eager,
  sizes = CARD_SIZES,
}: {
  bike: Bike
  className?: string
  eager?: boolean
  sizes?: string
}) {
  const [failed, setFailed] = useState(false)
  const src = bike.images[0]
  return (
    <div className={cn('relative overflow-hidden bg-muted', className)}>
      {src && !failed ? (
        <img
          src={src}
          srcSet={responsiveSrcSet(src)}
          sizes={sizes}
          alt={`${bike.year} ${bike.make} ${bike.model}`}
          loading={eager ? 'eager' : 'lazy'}
          decoding="async"
          onError={() => setFailed(true)}
          className="size-full object-cover"
        />
      ) : (
        <Illustration bike={bike} />
      )}
    </div>
  )
}
