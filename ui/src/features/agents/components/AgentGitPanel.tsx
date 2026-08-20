import { useCallback, useEffect, useMemo, useState } from "react"
import { DownloadIcon } from "lucide-react"

import type { AgentThread } from "@/features/agents/lib/types"
import type { PanelTabKind } from "@/features/agents/lib/panelTabs"
import { agentsApi } from "@/features/agents/lib/api"
import { useAgentThreadTurnDiff } from "@/features/agents/lib/queries"
import { AgentPanelShell } from "@/features/agents/components/AgentPanelShell"
import { ChangesPanel } from "@/features/agents/components/ChangesPanel"
import { toPanelFiles } from "@/features/agents/components/DiffFilesView"
import { TerminalPanel } from "@/features/agents/components/TerminalPanel"
import {
  AGENT_COMMON_TABS,
  AGENT_PANEL_KINDS,
  usePanelTabs,
} from "@/features/agents/lib/panelTabs"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { terminalTabTitle } from "@/features/agents/lib/terminalTabTitle"
import { useRegisterAppCommands } from "@/lib/appCommands"
import { cn } from "@/lib/utils"

interface AgentGitPanelProps {
  thread: AgentThread
  revealFilePath?: string | null
  revealChangesKey?: number
  collapsed: boolean
  onCollapsedChange: (next: boolean) => void
}

export function AgentGitPanel({
  thread,
  revealFilePath,
  revealChangesKey = 0,
  collapsed,
  onCollapsedChange,
}: AgentGitPanelProps) {
  const panel = usePanelTabs(`cloud:${thread.id}`, AGENT_COMMON_TABS)
  const terminals = useTerminalGroups(
    { kind: "cloud", threadId: thread.id },
    ""
  )
  const activeTabId = panel.activeTabId
  useEffect(() => {
    if (revealChangesKey > 0) panel.openChanges()
  }, [panel.openChanges, revealChangesKey])
  const terminalAvailable =
    thread.isOwner !== false && Boolean(thread.sandboxId)

  const handleOpenKind = useCallback(
    (kind: PanelTabKind) => {
      if (kind !== "terminal") return
      panel.open({ id: terminals.addGroup(), kind })
    },
    [panel, terminals]
  )
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
      if (id !== "changes" && (await terminals.closeGroup(id))) panel.close(id)
    },
    [panel, terminals]
  )
  const toggleTerminal = useCallback(() => {
    if (!collapsed && panel.activeTab?.kind === "terminal") {
      onCollapsedChange(true)
      return
    }
    onCollapsedChange(false)
    const existing = panel.tabs.find((tab) => tab.kind === "terminal")
    if (existing) handleSelectTab(existing.id)
    else handleOpenKind("terminal")
  }, [
    collapsed,
    handleOpenKind,
    handleSelectTab,
    onCollapsedChange,
    panel.activeTab?.kind,
    panel.tabs,
  ])

  useRegisterAppCommands(
    useMemo(
      () => [
        {
          id: "toggle-work-panel",
          label: "Toggle work panel",
          aliases: ["show panel", "hide panel", "changes panel"],
          shortcuts: ["mod+alt+b"],
          group: "Workspace",
          run: () => onCollapsedChange(!collapsed),
        },
        ...(terminalAvailable
          ? [
              {
                id: "toggle-terminal",
                label: "Toggle terminal",
                aliases: ["open terminal", "hide terminal"],
                shortcuts: ["ctrl+`"],
                group: "Workspace",
                run: toggleTerminal,
              },
            ]
          : []),
      ],
      [collapsed, onCollapsedChange, terminalAvailable, toggleTerminal]
    )
  )

  const terminalGroupIds = terminals.state.terminalGroups
    .map((group) => group.id)
    .join(",")
  const syncTerminals = panel.syncTerminals
  useEffect(() => {
    syncTerminals(terminalGroupIds ? terminalGroupIds.split(",") : [])
  }, [syncTerminals, terminalGroupIds])

  const turnDiff = useAgentThreadTurnDiff(
    thread.id,
    null,
    !collapsed && activeTabId === "changes",
    {},
    thread.status === "running"
  )
  const files = useMemo(
    () => toPanelFiles(turnDiff.data?.files ?? []),
    [turnDiff.data?.files]
  )
  const [recoveringPatch, setRecoveringPatch] = useState(false)
  const [recoveryError, setRecoveryError] = useState<string | null>(null)
  const canDownloadRecovery =
    thread.status !== "running" && thread.isOwner !== false
  const downloadRecoveryPatch = useCallback(async () => {
    setRecoveringPatch(true)
    setRecoveryError(null)
    try {
      const { blob, filename } = await agentsApi.downloadThreadRecoveryPatch(
        thread.id
      )
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    } catch (error) {
      setRecoveryError(
        error instanceof Error ? error.message : "Failed to download patch"
      )
    } finally {
      setRecoveringPatch(false)
    }
  }, [thread.id])

  return (
    <AgentPanelShell
      tabs={panel.tabs.map((tab) =>
        tab.kind === "terminal"
          ? { ...tab, title: terminalTabTitle(terminals, tab.id) }
          : tab
      )}
      activeTabId={activeTabId}
      onSelectTab={handleSelectTab}
      onCloseTab={handleCloseTab}
      onOpenKind={terminalAvailable ? handleOpenKind : undefined}
      menuKinds={AGENT_PANEL_KINDS}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
    >
      {({ fullScreen }) => (
        <>
          {activeTabId === "changes" && (
            <ChangesPanel
              files={files}
              status={turnDiff.data?.status}
              isLoading={turnDiff.isPending}
              isFetching={turnDiff.isFetching}
              error={turnDiff.error}
              truncated={turnDiff.data?.truncated}
              branch={thread.branch}
              pr={thread.pr}
              revealFilePath={revealFilePath}
              fullScreen={fullScreen}
              onRefresh={() => void turnDiff.refetch()}
              extraActions={
                canDownloadRecovery ? (
                  <button
                    type="button"
                    aria-label="Download recovery patch"
                    title={recoveryError ?? "Download recovery patch"}
                    disabled={recoveringPatch}
                    onClick={() => void downloadRecoveryPatch()}
                    className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
                  >
                    <DownloadIcon className="size-3.5" />
                  </button>
                ) : undefined
              }
            />
          )}
          {panel.tabs
            .filter((tab) => tab.kind === "terminal")
            .map((tab) => (
              <div
                key={tab.id}
                className={cn(
                  "min-h-0 flex-1",
                  tab.id !== activeTabId && "hidden"
                )}
              >
                <TerminalPanel
                  target={{ kind: "cloud", threadId: thread.id }}
                  cwd=""
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
