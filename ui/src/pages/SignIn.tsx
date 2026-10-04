import { useEffect, useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from '../auth'
import { pageTitle } from '../pageTitle'

export function SignIn() {
  const { key, signIn, notice } = useAuth()
  const location = useLocation()
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    document.title = pageTitle('/signin')
  }, [])

  if (key) {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from ?? '/'} replace />
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(value)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Sign-in failed.')
      setBusy(false)
    }
  }

  return (
    <main className="signin">
      <form className="card" onSubmit={submit} noValidate>
        <h1>OCPP Broker</h1>
        <p className="muted">Sign in with the broker&apos;s API key.</p>

        {notice && <p className="banner warn">{notice}</p>}

        <label htmlFor="api-key">API key</label>
        <input
          id="api-key"
          type="password"
          autoComplete="off"
          autoFocus
          spellCheck={false}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          aria-describedby={error ? 'api-key-error' : undefined}
          aria-invalid={error ? true : undefined}
        />
        {error && (
          <p id="api-key-error" role="alert" className="banner error">
            {error}
          </p>
        )}

        <button type="submit" disabled={busy}>
          {busy ? 'Checking…' : 'Sign in'}
        </button>

        <p className="muted small">
          This is the key set in <code>security.api_key</code> or <code>OCPP_BROKER_API_KEY</code>. Everyone who has it
          can do everything the API allows. It is kept for this browser tab only.
        </p>
      </form>
    </main>
  )
}
