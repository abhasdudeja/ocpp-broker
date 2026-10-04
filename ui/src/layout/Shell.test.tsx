import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { pageTitle } from '../pageTitle'
import { info, mockApi, org, renderApp, signedIn } from '../test-utils'

describe('page titles', () => {
  it.each([
    ['/', 'Overview'],
    ['/signin', 'Sign in'],
    ['/chargers', 'Chargers'],
    ['/chargers/Fleet/CP-001', 'Charger'],
    ['/backends', 'Backends'],
    ['/history', 'History'],
    ['/history/transactions/Fleet/CP-001/7', 'Transaction'],
    ['/tags', 'Tags'],
    ['/admin', 'Admin'],
    ['/admin/new', 'Add an organization'],
    ['/admin/orgs/Fleet', 'Organization'],
    ['/nowhere', 'Overview'],
  ])('%s is %s', (path, title) => {
    expect(pageTitle(path)).toBe(`${title} · OCPP Broker`)
  })
})

describe('the shell', () => {
  function open(path = '/') {
    signedIn()
    mockApi({ '/api/system/info': info(), '/api/orgs': [org()], '/api/chargers': { chargers: [], total: 0 }, '/api/history/info': { available: false, reason: 'x', messages_enabled: false, heartbeats_enabled: false, retention_days: {}, counts: {} } })
    renderApp(path)
  }

  it('names the tab after the page, and renames it when the page changes', async () => {
    open('/chargers')
    await screen.findByRole('heading', { name: 'Chargers' })
    expect(document.title).toBe('Chargers · OCPP Broker')
    await userEvent.click(screen.getByRole('link', { name: 'History' }))
    await screen.findByRole('heading', { name: 'History' })
    expect(document.title).toBe('History · OCPP Broker')
  })

  it('moves the keyboard to the start of a new page, but leaves the first page alone', async () => {
    open('/chargers')
    await screen.findByRole('heading', { name: 'Chargers' })
    expect(screen.getByRole('main')).not.toHaveFocus()
    await userEvent.click(screen.getByRole('link', { name: 'Backends' }))
    await screen.findByRole('heading', { name: 'Backends' })
    expect(screen.getByRole('main')).toHaveFocus()
  })

  it('has a link that skips the navigation', async () => {
    open('/chargers')
    await screen.findByRole('heading', { name: 'Chargers' })
    const skip = screen.getByRole('link', { name: 'Skip to the page' })
    expect(skip).toHaveAttribute('href', '#main')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
    await userEvent.click(skip)
    expect(screen.getByRole('main')).toHaveFocus()
    expect(screen.getByRole('main')).toHaveAttribute('tabindex', '-1')
  })

  it('puts the skip link first in the tab order', async () => {
    open('/chargers')
    await screen.findByRole('heading', { name: 'Chargers' })
    await userEvent.tab()
    expect(screen.getByRole('link', { name: 'Skip to the page' })).toHaveFocus()
  })
})
