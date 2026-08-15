import { useCallback, useEffect } from "react"

import type { PanelTabKind } from "@/features/agents/lib/panelTabs"
import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"
import { AgentPanelShell } from "@/features/agents/components/AgentPanelShell"
import { TerminalPanel } from "@/features/agents/components/TerminalPanel"
import { usePanelTabs } from "@/features/agents/lib/panelTabs"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { cn } from "@/lib/utils"

const NEW_AGENT_PANEL_KINDS: ReadonlyArray<PanelTabKind> = ["terminal"]

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
  const panel = usePanelTabs(sessionId)
  const terminals = useTerminalGroups({ kind: "local", sessionId }, cwd)

  const handleOpenKind = useCallback(() => {
    panel.open({ id: terminals.addGroup(), kind: "terminal" })
  }, [panel, terminals])
  const handleSelectTab = useCallback(
    (id: string) => {
      panel.select(id)
      const terminalId = terminals.state.terminalGroups.find(
        (group) => group.id === id
      )?.terminalIds[0]
      if (terminalId) terminals.focus(terminalId)
    },
    [panel, terminals]
  )
  const handleCloseTab = useCallback(
    async (id: string) => {
      if (!(await terminals.closeGroup(id))) return
      panel.close(id)
    },
    [panel, terminals]
  )

  const terminalGroupIds = terminals.state.terminalGroups
    .map((group) => group.id)
    .join(",")
  const syncTerminals = panel.syncTerminals
  useEffect(() => {
    syncTerminals(terminalGroupIds ? terminalGroupIds.split(",") : [])
  }, [syncTerminals, terminalGroupIds])

  return (
    <AgentPanelShell
      tabs={panel.tabs.map((tab) => ({
        ...tab,
        title: terminalTabTitle(terminals, tab.id),
      }))}
      activeTabId={panel.activeTabId}
      onSelectTab={handleSelectTab}
      onCloseTab={handleCloseTab}
      onOpenKind={handleOpenKind}
      menuKinds={NEW_AGENT_PANEL_KINDS}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
    >
      {() => (
        <>
          {panel.tabs.map((tab) => (
            <div
              key={tab.id}
              className={cn(
                "min-h-0 flex-1",
                tab.id !== panel.activeTabId && "hidden"
              )}
            >
              <TerminalPanel
                target={{ kind: "local", sessionId }}
                cwd={cwd}
                groupId={tab.id}
                terminals={terminals}
              />
            </div>
          ))}
        </>
      )}
    </AgentPanelShell>
  )
}

function terminalTabTitle(
  terminals: TerminalGroupsController,
  groupId: string
): string {
  const group = terminals.state.terminalGroups.find(
    (candidate) => candidate.id === groupId
  )
  const terminalId = group?.terminalIds.includes(
    terminals.state.activeTerminalId
  )
    ? terminals.state.activeTerminalId
    : group?.terminalIds[0]
  return (
    (terminalId ? terminals.metadataById.get(terminalId)?.label : null) ||
    "Terminal"
  )
}
