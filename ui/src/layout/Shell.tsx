import { useEffect, useRef } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '../auth'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { LiveIndicator } from '../components/LiveIndicator'
import { EventsProvider } from '../events'
import { pageTitle } from '../pageTitle'

export function Shell() {
  const { signOut } = useAuth()
  const location = useLocation()
  const main = useRef<HTMLElement>(null)
  const first = useRef(true)

  // A new page gets its own title, and keyboard and screen-reader users land at its start instead of on a link in
  // the page they left (the first page shown is left alone: the browser has already put them there)
  useEffect(() => {
    document.title = pageTitle(location.pathname)
    if (first.current) {
      first.current = false
      return
    }
    main.current?.focus({ preventScroll: true })
  }, [location.pathname])

  return (
    <EventsProvider>
      <div className="shell">
        <a className="skip-link" href="#main" onClick={(event) => { event.preventDefault(); main.current?.focus() }}>
          Skip to the page
        </a>
        <header className="topbar">
          <NavLink to="/" className="brand" end>
            OCPP Broker
          </NavLink>
          <nav aria-label="Main">
            <NavLink to="/" end>
              Overview
            </NavLink>
            <NavLink to="/chargers">Chargers</NavLink>
            <NavLink to="/backends">Backends</NavLink>
            <NavLink to="/history">History</NavLink>
            <NavLink to="/tags">Tags</NavLink>
            <NavLink to="/admin">Admin</NavLink>
          </nav>
          <LiveIndicator />
          <button type="button" className="link" onClick={signOut}>
            Sign out
          </button>
        </header>
        <main className="content" id="main" tabIndex={-1} ref={main}>
          {/* keyed by address, so leaving a page that failed gives the next one a clean start */}
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </EventsProvider>
  )
}
