import type { ReactNode } from "react"

export function AgentComposerDock({ children }: { children: ReactNode }) {
  return (
    <div
      className="shrink-0 px-space-4 pb-space-4"
      style={{ viewTransitionName: "agent-composer" }}
    >
      <div className="mx-auto w-full max-w-3xl min-w-0">{children}</div>
    </div>
  )
}
