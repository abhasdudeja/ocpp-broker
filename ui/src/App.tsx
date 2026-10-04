import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { useAuth } from './auth'
import { Shell } from './layout/Shell'
import { Admin } from './pages/Admin'
import { AdminOrg } from './pages/AdminOrg'
import { Backends } from './pages/Backends'
import { ChargerDetail } from './pages/ChargerDetail'
import { Chargers } from './pages/Chargers'
import { History } from './pages/History'
import { Overview } from './pages/Overview'
import { SignIn } from './pages/SignIn'
import { Tags } from './pages/Tags'
import { TransactionHistory } from './pages/TransactionHistory'

function RequireAuth({ children }: { children: ReactNode }) {
  const { key } = useAuth()
  const location = useLocation()
  if (!key) return <Navigate to="/signin" replace state={{ from: location.pathname + location.search }} />
  return children
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/signin" element={<SignIn />} />
      <Route
        element={
          <RequireAuth>
            <Shell />
          </RequireAuth>
        }
      >
        <Route index element={<Overview />} />
        <Route path="chargers" element={<Chargers />} />
        <Route path="chargers/:org/:chargerId" element={<ChargerDetail />} />
        <Route path="backends" element={<Backends />} />
        <Route path="history" element={<History />} />
        <Route path="history/transactions/:org/:chargerId/:transactionId" element={<TransactionHistory />} />
        <Route path="tags" element={<Tags />} />
        <Route path="admin" element={<Admin />} />
        <Route path="admin/new" element={<AdminOrg />} />
        <Route path="admin/orgs/:name" element={<AdminOrg />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
