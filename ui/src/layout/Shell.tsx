import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '../auth'
import { ErrorBoundary } from '../components/ErrorBoundary'

export function Shell() {
  const { signOut } = useAuth()
  const location = useLocation()
  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand" end>
          OCPP Broker
        </NavLink>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Overview
          </NavLink>
          <NavLink to="/chargers">Chargers</NavLink>
        </nav>
        <button type="button" className="link" onClick={signOut}>
          Sign out
        </button>
      </header>
      <main className="content">
        {/* keyed by address, so leaving a page that failed gives the next one a clean start */}
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  )
}
