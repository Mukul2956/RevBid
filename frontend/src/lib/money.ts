/** Money helpers. The API speaks integer paise; people speak rupees. */

const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
})

const grouped = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })

/** 12500000 -> "₹1,25,000" (Indian digit grouping). */
export function formatINR(paise: number): string {
  return inr.format(Math.round(paise / 100))
}

/** 12500000 -> "1,25,000" (no symbol, for inputs). */
export function formatRupees(paise: number): string {
  return grouped.format(Math.round(paise / 100))
}

/** "1,25,000" / "125000" / " ₹ 1,25,000 " -> 12500000 paise. Returns null if not a whole rupee amount. */
export function rupeesToPaise(input: string): number | null {
  const cleaned = input.replace(/[₹,\s]/g, '')
  if (!/^\d{1,10}$/.test(cleaned)) return null
  return Number(cleaned) * 100
}

export function paiseToRupees(paise: number): number {
  return Math.round(paise / 100)
}

/** 12500000 -> "₹1.25 L", 1500000000 -> "₹1.5 Cr". For dense tables and stat tiles. */
export function formatINRCompact(paise: number): string {
  const rupees = paise / 100
  if (rupees >= 1e7) return `₹${trim(rupees / 1e7)} Cr`
  if (rupees >= 1e5) return `₹${trim(rupees / 1e5)} L`
  return formatINR(paise)
}

function trim(n: number): string {
  return n.toFixed(2).replace(/\.?0+$/, '')
}

export function formatKm(km: number): string {
  return `${grouped.format(km)} km`
}

export function ordinal(n: number): string {
  if (n === 1) return '1st owner'
  if (n === 2) return '2nd owner'
  if (n === 3) return '3rd owner'
  return `${n}th owner`
}
