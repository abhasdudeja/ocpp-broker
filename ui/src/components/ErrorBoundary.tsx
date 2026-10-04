import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  failed: boolean
}

/** If a page fails while rendering, say so and offer a way out, rather than leaving a blank screen. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Only to the browser console; nothing is sent anywhere
    console.error('The console could not show this page:', error, info.componentStack)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div role="alert" className="banner error">
        <p>
          <strong>This page could not be shown.</strong> Something unexpected came back from the broker, or the console has a bug.
        </p>
        <button type="button" onClick={() => window.location.reload()}>
          Reload the console
        </button>
      </div>
    )
  }
}
