import { MCPConnectionsSection } from "./MCPConnectionsSection"

export function LocalMCPSection() {
  if (typeof window === "undefined" || !window.openSweDesktop) return null
  return <MCPConnectionsSection scope="local" />
}
