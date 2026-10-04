import { NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '../auth'

export function Shell() {
  const { signOut } = useAuth()
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
        </nav>
        <button type="button" className="link" onClick={signOut}>
          Sign out
        </button>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
