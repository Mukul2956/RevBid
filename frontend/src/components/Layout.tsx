import * as Menu from '@radix-ui/react-dropdown-menu'
import { Bike, Gavel, LayoutDashboard, LogOut, Moon, Sun, User as UserIcon } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useNavigationType } from 'react-router-dom'
import { toast } from 'sonner'
import { ButtonLink } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/cn'
import { formatINR } from '@/lib/money'
import { useRealtimeNotifications } from '@/lib/realtime'
import { useTheme } from '@/lib/theme'

/**
 * A plain <BrowserRouter> keeps the previous scroll position, so tapping a card halfway down a list
 * would open the detail page scrolled mid-way. Reset on forward navigation to a new path; leave
 * back/forward (POP) to the browser's own scroll restoration, and ignore filter/query changes.
 */
function ScrollToTop() {
  const { pathname } = useLocation()
  const navigationType = useNavigationType()
  const previous = useRef(pathname)
  useEffect(() => {
    // Only a *path* change counts: a filter change replaces the query string (navigationType flips to
    // REPLACE) but must not throw the user back to the top of the list.
    if (previous.current !== pathname && navigationType !== 'POP') window.scrollTo({ top: 0, left: 0 })
    previous.current = pathname
  }, [pathname, navigationType])
  return null
}

function Logo() {
  return (
    <Link to="/" className="flex items-center gap-2 text-lg font-extrabold tracking-tight">
      <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
        <Bike className="size-5" aria-hidden />
      </span>
      RevBid
    </Link>
  )
}

const navLink = ({ isActive }: { isActive: boolean }) =>
  cn(
    'rounded-lg px-3 py-2 text-sm font-semibold transition-colors',
    isActive ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground',
  )

function UserMenu() {
  const { user, isAdmin, logout } = useAuth()
  const navigate = useNavigate()
  if (!user) return null
  return (
    <Menu.Root>
      <Menu.Trigger
        className="flex cursor-pointer items-center gap-2 rounded-full border border-border bg-card py-1 pr-3 pl-1 text-sm font-semibold hover:bg-muted"
        aria-label="Account menu"
      >
        <span className="grid size-7 place-items-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
          {user.display_name.charAt(0).toUpperCase()}
        </span>
        <span className="hidden max-w-28 truncate sm:inline">{user.display_name}</span>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="end"
          sideOffset={8}
          className="z-50 min-w-52 rounded-xl border border-border bg-card p-1.5 shadow-lg"
        >
          <div className="px-3 py-2">
            <p className="truncate text-sm font-semibold">{user.display_name}</p>
            <p className="truncate text-xs text-muted-foreground">{user.email}</p>
          </div>
          <Menu.Separator className="my-1 h-px bg-border" />
          {!isAdmin && (
            <Menu.Item
              onSelect={() => navigate('/my-bids')}
              className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm outline-none data-[highlighted]:bg-muted"
            >
              <Gavel className="size-4" aria-hidden /> My bids
            </Menu.Item>
          )}
          {isAdmin && (
            <Menu.Item
              onSelect={() => navigate('/admin')}
              className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm outline-none data-[highlighted]:bg-muted"
            >
              <LayoutDashboard className="size-4" aria-hidden /> Admin console
            </Menu.Item>
          )}
          <Menu.Item
            onSelect={() => void logout().then(() => navigate('/'))}
            className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm text-danger outline-none data-[highlighted]:bg-muted"
          >
            <LogOut className="size-4" aria-hidden /> Log out
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  )
}

export function Layout() {
  const { user, status, isAdmin } = useAuth()
  const { theme, toggle } = useTheme()
  const navigate = useNavigate()

  // Private realtime events surface as toasts wherever the user happens to be.
  useRealtimeNotifications((event) => {
    if (event.type === 'outbid') {
      toast.warning(`You've been outbid on ${event.data.title}`, {
        description: `New high bid: ${formatINR(event.data.current_price)}`,
        action: { label: 'Bid again', onClick: () => navigate(`/auctions/${event.auction_id}`) },
        duration: 10_000,
      })
    } else {
      toast.success(`You won ${event.data.title}!`, {
        description: `Final price ${formatINR(event.data.final_price)}`,
        action: { label: 'View', onClick: () => navigate(`/auctions/${event.auction_id}`) },
        duration: 15_000,
      })
    }
  })

  return (
    <div className="flex min-h-dvh flex-col">
      <ScrollToTop />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-border bg-background/85 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:px-6">
          <Logo />
          <nav className="ml-2 hidden items-center gap-1 sm:flex" aria-label="Main">
            <NavLink to="/" end className={navLink}>
              Auctions
            </NavLink>
            {user && !isAdmin && (
              <NavLink to="/my-bids" className={navLink}>
                My bids
              </NavLink>
            )}
            {isAdmin && (
              <NavLink to="/admin" className={navLink}>
                Admin
              </NavLink>
            )}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={toggle}
              className="grid size-9 cursor-pointer place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            >
              {theme === 'dark' ? <Sun className="size-5" /> : <Moon className="size-5" />}
            </button>
            {status === 'loading' ? (
              <div className="skeleton h-9 w-24 rounded-full" aria-hidden />
            ) : user ? (
              <UserMenu />
            ) : (
              <>
                <ButtonLink to="/login" className="hidden sm:inline-flex" variant="ghost" size="sm">
                    Log in
                  </ButtonLink>
                <ButtonLink to="/register" size="sm">
                    <UserIcon className="size-4" aria-hidden />
                    Sign up
                  </ButtonLink>
              </>
            )}
          </div>
        </div>
        {/* phones: nav as a second row */}
        <nav className="flex gap-1 overflow-x-auto border-t border-border px-4 py-1.5 sm:hidden" aria-label="Main (mobile)">
          <NavLink to="/" end className={navLink}>
            Auctions
          </NavLink>
          {user && !isAdmin && (
            <NavLink to="/my-bids" className={navLink}>
              My bids
            </NavLink>
          )}
          {isAdmin && (
            <NavLink to="/admin" className={navLink}>
              Admin
            </NavLink>
          )}
          {!user && (
            <NavLink to="/login" className={navLink}>
              Log in
            </NavLink>
          )}
        </nav>
      </header>

      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <Outlet />
      </main>

      <footer className="border-t border-border py-6 text-center text-xs text-muted-foreground">
        RevBid · live used-bike auctions · prices in INR ·{' '}
        <Link className="underline hover:text-foreground" to="/credits">
          Photo credits
        </Link>{' '}
        ·{' '}
        <a className="underline hover:text-foreground" href="/docs" target="_blank" rel="noreferrer">
          API docs
        </a>
      </footer>
    </div>
  )
}
