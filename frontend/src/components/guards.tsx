import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Spinner } from '@/components/ui/misc'
import { useAuth } from '@/lib/auth'

/** Gate a route subtree on being signed in (and optionally an administrator). UX only: the API enforces. */
export function RequireAuth({ admin = false }: { admin?: boolean }) {
  const { user, status, isAdmin } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return (
      <div className="grid place-items-center py-24">
        <Spinner label="Restoring your session" />
      </div>
    )
  }
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />
  if (admin && !isAdmin) return <Navigate to="/" replace />
  return <Outlet />
}
