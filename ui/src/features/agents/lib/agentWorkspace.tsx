import { createContext, useContext } from "react"
import type { ReactNode } from "react"

const AgentWorkspaceContext = createContext(false)

export function AgentWorkspaceProvider({
  enabled,
  children,
}: {
  enabled: boolean
  children: ReactNode
}) {
  return (
    <AgentWorkspaceContext.Provider value={enabled}>
      {children}
    </AgentWorkspaceContext.Provider>
  )
}

export function useAgentWorkspace() {
  return useContext(AgentWorkspaceContext)
}
