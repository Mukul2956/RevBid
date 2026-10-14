import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { RequireAuth } from '@/components/guards'
import { Spinner } from '@/components/ui/misc'
import AuctionDetailPage from '@/pages/AuctionDetailPage'
import AuctionsPage from '@/pages/AuctionsPage'
import CreditsPage from '@/pages/CreditsPage'
import { LoginPage, RegisterPage } from '@/pages/AuthPages'
import MyBidsPage from '@/pages/MyBidsPage'
import NotFoundPage from '@/pages/NotFoundPage'

// The admin console is a separate chunk: bidders never download it.
const AdminLayout = lazy(() => import('@/pages/admin/AdminLayout'))
const DashboardPage = lazy(() => import('@/pages/admin/DashboardPage'))
const AdminAuctionsPage = lazy(() => import('@/pages/admin/AdminAuctionsPage'))
const AuctionFormPage = lazy(() => import('@/pages/admin/AuctionFormPage'))
const AdminAuctionDetailPage = lazy(() => import('@/pages/admin/AdminAuctionDetailPage'))
const BikesPage = lazy(() => import('@/pages/admin/BikesPage'))
const SystemPage = lazy(() => import('@/pages/admin/SystemPage'))

const fallback = (
  <div className="grid place-items-center py-24">
    <Spinner />
  </div>
)

export default function App() {
  return (
    <Suspense fallback={fallback}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<AuctionsPage />} />
          <Route path="auctions/:id" element={<AuctionDetailPage />} />
          <Route path="login" element={<LoginPage />} />
          <Route path="register" element={<RegisterPage />} />
          <Route path="credits" element={<CreditsPage />} />

          <Route element={<RequireAuth />}>
            <Route path="my-bids" element={<MyBidsPage />} />
          </Route>

          <Route element={<RequireAuth admin />}>
            <Route path="admin" element={<AdminLayout />}>
              <Route index element={<DashboardPage />} />
              <Route path="auctions" element={<AdminAuctionsPage />} />
              <Route path="auctions/new" element={<AuctionFormPage />} />
              <Route path="auctions/:id" element={<AdminAuctionDetailPage />} />
              <Route path="bikes" element={<BikesPage />} />
              <Route path="system" element={<SystemPage />} />
            </Route>
          </Route>

          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
