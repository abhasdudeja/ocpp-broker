import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ErrorBoundary } from './ErrorBoundary'

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error('rendering went wrong')
  return <p>all fine</p>
}

describe('ErrorBoundary', () => {
  it('shows its children when nothing goes wrong', () => {
    render(
      <ErrorBoundary>
        <Bomb explode={false} />
      </ErrorBoundary>,
    )
    expect(screen.getByText('all fine')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('replaces a page that fails to render with a message and a way to reload', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {})
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    render(
      <ErrorBoundary>
        <Bomb explode />
      </ErrorBoundary>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('This page could not be shown.')
    expect(screen.queryByText('all fine')).not.toBeInTheDocument()
    expect(log).toHaveBeenCalled()

    await userEvent.setup().click(screen.getByRole('button', { name: 'Reload the console' }))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('starts clean when it is given a new key, as the shell does when the address changes', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    function Host() {
      const [page, setPage] = useState('broken')
      return (
        <>
          <button type="button" onClick={() => setPage('fine')}>
            go
          </button>
          <ErrorBoundary key={page}>
            <Bomb explode={page === 'broken'} />
          </ErrorBoundary>
        </>
      )
    }
    render(<Host />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'go' }))
    expect(screen.getByText('all fine')).toBeInTheDocument()
  })
})
