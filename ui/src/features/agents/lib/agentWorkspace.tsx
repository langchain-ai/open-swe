import { createContext, useContext, useEffect } from "react"
import type { ReactNode } from "react"

const AgentWorkspaceContext = createContext(false)

export function AgentWorkspaceProvider({
  enabled,
  children,
}: {
  enabled: boolean
  children: ReactNode
}) {
  useEffect(() => {
    if (!enabled) return
    document.documentElement.setAttribute("data-agent-workspace", "")
    return () =>
      document.documentElement.removeAttribute("data-agent-workspace")
  }, [enabled])

  return (
    <AgentWorkspaceContext.Provider value={enabled}>
      {children}
    </AgentWorkspaceContext.Provider>
  )
}

export function useAgentWorkspace() {
  return useContext(AgentWorkspaceContext)
}
