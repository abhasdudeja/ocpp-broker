import { act, renderHook, screen, render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ShowMore } from './components/ShowMore'
import { useShowMore } from './useShowMore'

const items = Array.from({ length: 250 }, (_, n) => n)

describe('showing a long list a part at a time', () => {
  it('shows the first hundred and says how many are left', () => {
    const { result } = renderHook(() => useShowMore(items, 'a'))
    expect(result.current.visible).toHaveLength(100)
    expect(result.current.visible[99]).toBe(99)
    expect(result.current.hidden).toBe(150)
  })

  it('shows a hundred more each time, and then all', () => {
    const { result } = renderHook(() => useShowMore(items, 'a'))
    act(() => result.current.showMore())
    expect(result.current.visible).toHaveLength(200)
    act(() => result.current.showMore())
    expect(result.current.visible).toHaveLength(250)
    expect(result.current.hidden).toBe(0)
    act(() => result.current.showMore())
    expect(result.current.visible).toHaveLength(250)
  })

  it('starts again when the key changes', () => {
    const { result, rerender } = renderHook(({ key }) => useShowMore(items, key), { initialProps: { key: 'a' } })
    act(() => result.current.showMore())
    expect(result.current.visible).toHaveLength(200)
    rerender({ key: 'b' })
    expect(result.current.visible).toHaveLength(100)
  })

  it('keeps what has been revealed while the list itself is refreshed', () => {
    const { result, rerender } = renderHook(({ list }) => useShowMore(list, 'a'), { initialProps: { list: items } })
    act(() => result.current.showMore())
    rerender({ list: [...items, 250, 251] })
    expect(result.current.visible).toHaveLength(200)
    expect(result.current.hidden).toBe(52)
  })

  it('takes a step of its own', () => {
    const { result } = renderHook(() => useShowMore(items, 'a', 10))
    expect(result.current.visible).toHaveLength(10)
    act(() => result.current.showMore())
    expect(result.current.visible).toHaveLength(20)
  })

  it('has nothing to hide in a short list', () => {
    const { result } = renderHook(() => useShowMore([1, 2, 3], 'a'))
    expect(result.current.visible).toEqual([1, 2, 3])
    expect(result.current.hidden).toBe(0)
  })
})

describe('the button under it', () => {
  it('offers up to a hundred more and says how many are not shown', async () => {
    const onClick = vi.fn()
    render(<ShowMore hidden={150} onClick={onClick} noun="more chargers" />)
    await userEvent.click(screen.getByRole('button', { name: 'Show 100 more chargers' }))
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(screen.getByText('150 not shown')).toBeInTheDocument()
  })

  it('offers only what is left at the end', () => {
    render(<ShowMore hidden={7} onClick={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Show 7 more' })).toBeInTheDocument()
  })

  it('is not there when nothing is hidden', () => {
    const { container } = render(<ShowMore hidden={0} onClick={() => undefined} />)
    expect(container).toBeEmptyDOMElement()
  })
})
