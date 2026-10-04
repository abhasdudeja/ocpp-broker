/** The button under a list that shows only part of its rows. */
export function ShowMore({ hidden, onClick, noun = 'more' }: { hidden: number; onClick: () => void; noun?: string }) {
  if (hidden <= 0) return null
  return (
    <p>
      <button type="button" className="secondary" onClick={onClick}>
        Show {Math.min(hidden, 100)} {noun}
      </button>{' '}
      <span className="muted small">{hidden} not shown</span>
    </p>
  )
}
