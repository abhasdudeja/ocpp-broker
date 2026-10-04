import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Pages are polled and streamed; on a machine running twenty test workers the default second is not always enough
configure({ asyncUtilTimeout: 4000 })

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  window.sessionStorage.clear()
})
