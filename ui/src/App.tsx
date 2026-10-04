import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { useAuth } from './auth'
import { Shell } from './layout/Shell'
import { ChargerDetail } from './pages/ChargerDetail'
import { Chargers } from './pages/Chargers'
import { Overview } from './pages/Overview'
import { SignIn } from './pages/SignIn'

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
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
