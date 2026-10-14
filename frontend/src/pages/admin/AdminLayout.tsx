import { Activity, Bike, Gavel, LayoutDashboard } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { cn } from '@/lib/cn'
import { useDocumentTitle } from '@/lib/hooks'

const items = [
  { to: '/admin', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/admin/auctions', label: 'Auctions', icon: Gavel, end: false },
  { to: '/admin/bikes', label: 'Bikes', icon: Bike, end: false },
  { to: '/admin/system', label: 'System', icon: Activity, end: false },
]

export default function AdminLayout() {
  useDocumentTitle('Admin')
  return (
    <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
      <nav aria-label="Admin" className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
        <p className="hidden px-3 pb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase lg:block">
          Admin console
        </p>
        {items.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-semibold whitespace-nowrap transition-colors',
                isActive ? 'bg-muted text-foreground' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
              )
            }
          >
            <Icon className="size-4" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="min-w-0">
        <Outlet />
      </div>
    </div>
  )
}
