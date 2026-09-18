import { createContext, useContext } from "react"
import type { ReactNode } from "react"

/**
 * Whether the subtree renders an active thread (`/agents/$threadId`). Shared
 * UI such as the prompt bar is mounted under the whole `/agents` layout, so it
 * needs this to tell a live thread from the home page.
 */
const AgentThreadStreamBoundaryContext = createContext(false)

export function useIsInAgentThreadStream(): boolean {
  return useContext(AgentThreadStreamBoundaryContext)
}

export function AgentThreadStreamBoundary({
  active = true,
  children,
}: {
  active?: boolean
  children: ReactNode
}) {
  return (
    <AgentThreadStreamBoundaryContext.Provider value={active}>
      {children}
    </AgentThreadStreamBoundaryContext.Provider>
  )
}
