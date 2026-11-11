import { Component, type ReactNode } from "react"
import { Button } from "@langchain/macaw-components/Button"

/** Keeps a game that failed to load or crashed from taking the chat page down with it. */
export class GameBoundary extends Component<
  { readonly onExit: () => void; readonly children: ReactNode },
  { readonly failed: boolean }
> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="grid size-full place-items-center bg-surface-level-1">
        <div className="flex flex-col items-center gap-space-3 text-center">
          <p className="text-secondary">Trace Flight couldn't load.</p>
          <Button
            size="sm"
            variant="outlined"
            color="secondary"
            onClick={this.props.onExit}
          >
            Back to chat
          </Button>
        </div>
      </div>
    )
  }
}
