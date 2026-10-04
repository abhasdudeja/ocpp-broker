import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

import { ApiError, apiGet, forgetKey, loadKey, saveKey, type SystemInfo } from './api/client'

interface Auth {
  /** The API key, or null when signed out. */
  key: string | null
  /** Check the key with the broker and, if it is accepted, keep it for this tab. Throws a readable Error. */
  signIn: (key: string) => Promise<void>
  signOut: () => void
  /** A page calls this when the broker answers 401: the key was changed or removed. */
  keyRejected: () => void
  /** Why the user was signed out, if the broker took the key away. */
  notice: string | null
}

const AuthContext = createContext<Auth | undefined>(undefined)

export function describeSignInError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'The broker did not accept that key.'
    if (error.status === 503) {
      return 'The broker has no API key configured. Set security.api_key or OCPP_BROKER_API_KEY on the broker and restart it.'
    }
    if (error.status === 0) return 'Could not reach the broker. Check that it is running and that this address is right.'
    return `The broker answered with an error: ${error.message}`
  }
  return 'Something went wrong while checking the key.'
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [key, setKey] = useState<string | null>(loadKey)
  const [notice, setNotice] = useState<string | null>(null)

  const signIn = useCallback(async (candidate: string) => {
    const trimmed = candidate.trim()
    if (!trimmed) throw new Error('Enter the API key.')
    try {
      // The same cheap authenticated call the overview page makes: 200 means the key is good
      await apiGet<SystemInfo>('/api/system/info', trimmed)
    } catch (error) {
      throw new Error(describeSignInError(error), { cause: error })
    }
    saveKey(trimmed)
    setNotice(null)
    setKey(trimmed)
  }, [])

  const signOut = useCallback(() => {
    forgetKey()
    setNotice(null)
    setKey(null)
  }, [])

  const keyRejected = useCallback(() => {
    forgetKey()
    setNotice('The broker no longer accepts this key. Sign in again.')
    setKey(null)
  }, [])

  const value = useMemo(() => ({ key, signIn, signOut, keyRejected, notice }), [key, signIn, signOut, keyRejected, notice])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth must be used inside <AuthProvider>')
  return auth
}
