import { useCallback, useEffect, useMemo } from "react"

import { RightPanelShell } from "@/features/agents/components/RightPanelShell"
import { TerminalPanel } from "@/features/agents/components/TerminalPanel"
import { useRightPanelStore } from "@/features/agents/lib/rightPanelStore"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { terminalTabTitle } from "@/features/agents/lib/terminalTabTitle"
import { cn } from "@/lib/utils"

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
  const panel = useRightPanelStore(`draft:${sessionId}`)
  const terminals = useTerminalGroups({ kind: "local", sessionId }, cwd)
  const openTerminal = useCallback(() => {
    panel.openTerminal(terminals.addGroup())
    onCollapsedChange(false)
  }, [onCollapsedChange, panel, terminals])
  const ids = terminals.state.terminalGroups.map((group) => group.id).join(",")
  useEffect(() => {
    panel.reconcileTerminals(ids ? ids.split(",") : [])
  }, [ids, panel.reconcileTerminals])
  const labels = useMemo(
    () =>
      new Map(
        terminals.state.terminalGroups.map((group) => [
          group.id,
          terminalTabTitle(terminals, group.id),
        ])
      ),
    [terminals]
  )
  const close = useCallback(
    async (surface: (typeof panel.surfaces)[number]) => {
      if (surface.kind !== "terminal") return
      if (await terminals.closeGroup(surface.resourceId))
        panel.closeSurface(surface.id)
    },
    [panel, terminals]
  )
  return (
    <RightPanelShell
      surfaces={panel.surfaces}
      activeSurfaceId={panel.activeSurfaceId}
      terminalLabels={labels}
      launcherItems={[
        {
          kind: "terminal",
          available: true,
          unavailableHint: "Available when a workspace is connected.",
          disabledReason: "Terminal is unavailable.",
        },
      ]}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      onActivate={(surface) => panel.activate(surface.id)}
      onClose={close}
      onCloseOthers={async (surface) => {
        for (const entry of panel.surfaces)
          if (entry.id !== surface.id) await close(entry)
      }}
      onCloseToRight={async (surface) => {
        const index = panel.surfaces.findIndex(
          (entry) => entry.id === surface.id
        )
        for (const entry of panel.surfaces.slice(index + 1)) await close(entry)
      }}
      onCloseAll={async () => {
        for (const surface of panel.surfaces) await close(surface)
      }}
      onOpen={openTerminal}
    >
      {panel.surfaces
        .filter((surface) => surface.kind === "terminal")
        .map((surface) => (
          <div
            key={surface.id}
            className={cn(
              "min-h-0 flex-1",
              surface.id !== panel.activeSurfaceId && "hidden"
            )}
          >
            <TerminalPanel
              target={{ kind: "local", sessionId }}
              cwd={cwd}
              groupId={surface.resourceId}
              terminals={terminals}
            />
          </div>
        ))}
    </RightPanelShell>
  )
}
