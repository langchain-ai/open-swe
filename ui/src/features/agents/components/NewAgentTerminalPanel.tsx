import { useEffect, useMemo } from "react"

import { AgentRightPanel } from "@/features/agents/components/panel/AgentRightPanel"
import { useRightPanelStore } from "@/features/agents/lib/rightPanelStore"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"

export function NewAgentTerminalPanel({
  sessionId,
  cwd,
  collapsed,
  onCollapsedChange,
}: {
  sessionId: string
  cwd: string
  collapsed: boolean
  onCollapsedChange: (next: boolean) => void
}) {
  const threadRef = useMemo(
    () => ({ scope: "local" as const, threadId: sessionId }),
    [sessionId]
  )
  const terminals = useTerminalGroups({ kind: "local", sessionId }, cwd)
  const openTerminal = useRightPanelStore((state) => state.openTerminal)
  const surfaceCount = useRightPanelStore(
    (state) => state.byThreadKey[`local:${sessionId}`]?.surfaces.length ?? 0
  )

  // The new-agent screen has nothing to diff or review yet, so the panel opens
  // straight into a terminal rather than the surface launcher.
  useEffect(() => {
    if (surfaceCount > 0) return
    openTerminal(threadRef, terminals.addGroup())
    // Only on the first mount for this session; adding `terminals` here would
    // spawn a second pty whenever the controller identity changes.
  }, [])

  return (
    <AgentRightPanel
      threadRef={threadRef}
      terminals={terminals}
      terminalTarget={{ kind: "local", sessionId }}
      cwd={cwd}
      terminalAvailable
      diffAvailable={false}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      renderDiff={() => null}
    />
  )
}
