import { useCallback, useEffect, useMemo, useState } from "react"
import {
  CheckIcon,
  ChevronDownIcon,
  DownloadIcon,
  EllipsisIcon,
  GitPullRequestIcon,
  RefreshCwIcon,
  TextAlignStartIcon,
} from "lucide-react"

import type { AgentThread } from "@/features/agents/lib/types"
import type { PanelTabKind } from "@/features/agents/lib/panelTabs"
import { agentsApi } from "@/features/agents/lib/api"
import { useAgentThreadTurnDiff } from "@/features/agents/lib/queries"
import { ReviewTab } from "@/features/reviews/components/ReviewTab"
import { AgentPanelShell } from "@/features/agents/components/AgentPanelShell"
import {
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from "@/components/ui/menu"
import { useDiffWrap } from "@/features/agents/utils/diffUtils"
import {
  DiffFilesView,
  toPanelFiles,
} from "@/features/agents/components/DiffFilesView"
import { TerminalPanel } from "@/features/agents/components/TerminalPanel"
import { usePanelTabs } from "@/features/agents/lib/panelTabs"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { terminalTabTitle } from "@/features/agents/lib/terminalTabTitle"
import { useRegisterAppCommands } from "@/lib/appCommands"
import { cn } from "@/lib/utils"

export type AgentPanelTab = "git"

interface AgentGitPanelProps {
  thread: AgentThread
  /** Path to select and scroll to, set when a transcript row is clicked. */
  revealFilePath?: string | null
  collapsed: boolean
  requestedTab: AgentPanelTab
  onCollapsedChange: (next: boolean) => void
  onTabChange: (tab: AgentPanelTab) => void
}

export function AgentGitPanel({
  thread,
  revealFilePath,
  collapsed,
  requestedTab,
  onCollapsedChange,
  onTabChange,
}: AgentGitPanelProps) {
  const [tab, setTab] = useState<"diff" | "review" | "commits">("diff")
  const [wrap, setWrap] = useDiffWrap()
  const panel = usePanelTabs(`cloud:${thread.id}`)
  const terminals = useTerminalGroups(
    { kind: "cloud", threadId: thread.id },
    ""
  )
  const topTab =
    panel.activeTab?.kind === "terminal" ? panel.activeTab.id : requestedTab
  const handleOpenKind = useCallback(
    (kind: PanelTabKind) => {
      if (kind !== "terminal") return
      onTabChange("git")
      panel.open({ id: terminals.addGroup(), kind })
    },
    [onTabChange, panel, terminals]
  )
  const handleSelectTab = useCallback(
    (id: string) => {
      if (id === "git") {
        onTabChange(id)
        panel.select("")
        return
      }
      panel.select(id)
      const terminalId = terminals.state.terminalGroups.find(
        (group) => group.id === id
      )?.terminalIds[0]
      if (terminalId) terminals.focus(terminalId)
    },
    [onTabChange, panel, terminals]
  )
  const handleCloseTab = useCallback(
    async (id: string) => {
      if (id === "git") {
        onCollapsedChange(true)
        return
      }
      if (await terminals.closeGroup(id)) panel.close(id)
    },
    [onCollapsedChange, panel, terminals]
  )
  const terminalAvailable =
    thread.isOwner !== false && Boolean(thread.sandboxId)
  const toggleTerminal = useCallback(() => {
    if (!collapsed && panel.activeTab?.kind === "terminal") {
      onCollapsedChange(true)
      return
    }
    onCollapsedChange(false)
    onTabChange("git")
    const existing = panel.tabs.find(
      (candidate) => candidate.kind === "terminal"
    )
    if (existing) handleSelectTab(existing.id)
    else handleOpenKind("terminal")
  }, [
    collapsed,
    handleOpenKind,
    handleSelectTab,
    onCollapsedChange,
    onTabChange,
    panel.activeTab?.kind,
    panel.tabs,
  ])
  const panelCommands = useMemo(
    () => [
      {
        id: "toggle-work-panel",
        label: "Toggle work panel",
        aliases: ["show panel", "hide panel", "review panel"],
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
  useRegisterAppCommands(panelCommands)
  const terminalGroupIds = terminals.state.terminalGroups
    .map((group) => group.id)
    .join(",")
  const syncTerminals = panel.syncTerminals
  useEffect(() => {
    syncTerminals(terminalGroupIds ? terminalGroupIds.split(",") : [])
  }, [syncTerminals, terminalGroupIds])
  // Collapsed state is owned and persisted by the parent.
  const setCollapsed = onCollapsedChange

  const pr = thread.pr

  // The open/closed state is persisted to localStorage, so it carries across
  // threads and reloads. Still uncollapse when a PR lands mid-session.
  const [prSeen, setPrSeen] = useState<{ threadId: string; hadPr: boolean }>(
    () => ({ threadId: thread.id, hadPr: Boolean(pr) })
  )
  if (prSeen.threadId !== thread.id) {
    setPrSeen({ threadId: thread.id, hadPr: Boolean(pr) })
  } else if (pr && !prSeen.hadPr) {
    setPrSeen({ threadId: thread.id, hadPr: true })
    setCollapsed(false)
  }

  const turnDiff = useAgentThreadTurnDiff(thread.id, null, !collapsed)
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

  const files = useMemo(
    () => toPanelFiles(turnDiff.data?.files ?? []),
    [turnDiff.data]
  )

  const totals = useMemo(
    () =>
      files.reduce(
        (sum, file) => ({
          additions: sum.additions + file.additions,
          deletions: sum.deletions + file.deletions,
        }),
        { additions: 0, deletions: 0 }
      ),
    [files]
  )
  const truncated = turnDiff.data?.truncated
  const tabLabels = { diff: "Thread", review: "Review", commits: "Committed" }
  const refreshDiff = () => void turnDiff.refetch()

  const reviewHeader = (
    <div className="shrink-0 px-3 pb-2">
      <div className="@container flex min-h-9 items-center gap-2">
        <Menu>
          <MenuTrigger className="flex items-center gap-1 rounded-md px-2 py-1 text-sm font-medium text-foreground transition-colors hover:bg-accent">
            {tabLabels[tab]}
            <ChevronDownIcon className="size-3.5 text-muted-foreground" />
          </MenuTrigger>
          <MenuPopup align="start" className="w-44">
            {(
              [
                ["diff", "Branch"],
                ["review", "Review"],
                ["commits", "Committed"],
              ] as const
            ).map(([id, label]) => (
              <MenuItem key={id} onClick={() => setTab(id)}>
                <span className="flex-1">{label}</span>
                {tab === id && <CheckIcon />}
              </MenuItem>
            ))}
          </MenuPopup>
        </Menu>
        {files.length > 0 && (
          <span className="flex items-center gap-2 text-sm">
            <span
              title={truncated ? "Only the first files are shown" : undefined}
              className="text-xs text-muted-foreground"
            >
              {truncated ? "first " : ""}
              {files.length} file{files.length === 1 ? "" : "s"}
            </span>
            <span className="text-success-foreground">+{totals.additions}</span>
            <span className="text-destructive">-{totals.deletions}</span>
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {recoveryError && (
            <span
              title={recoveryError}
              className="max-w-32 truncate text-[11px] text-destructive"
            >
              {recoveryError}
            </span>
          )}
          {pr && (
            <a
              href={pr.url}
              target="_blank"
              rel="noreferrer"
              aria-label="View PR"
              className="flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-border px-2 text-xs font-medium text-foreground transition-colors hover:bg-accent"
            >
              <GitPullRequestIcon className="size-3.5" />
              <span className="hidden @[700px]:inline">View PR</span>
            </a>
          )}
          <Menu>
            <MenuTrigger
              aria-label="Review options"
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <EllipsisIcon className="size-4" />
            </MenuTrigger>
            <MenuPopup align="end" className="w-48">
              <MenuItem onClick={refreshDiff}>
                <RefreshCwIcon />
                Refresh
              </MenuItem>
              <MenuItem onClick={() => setWrap(!wrap)}>
                <TextAlignStartIcon />
                {wrap ? "Disable" : "Enable"} word wrap
              </MenuItem>
              {canDownloadRecovery && (
                <>
                  <MenuSeparator />
                  <MenuItem
                    disabled={recoveringPatch}
                    onClick={downloadRecoveryPatch}
                  >
                    <DownloadIcon />
                    {recoveringPatch ? "Preparing…" : "Download patch"}
                  </MenuItem>
                </>
              )}
            </MenuPopup>
          </Menu>
        </div>
      </div>
    </div>
  )

  return (
    <AgentPanelShell
      tabs={[
        { id: "git", kind: "review" as const, closable: false },
        ...panel.tabs.map((panelTab) => ({
          ...panelTab,
          title: terminalTabTitle(terminals, panelTab.id),
        })),
      ]}
      activeTabId={topTab}
      onSelectTab={handleSelectTab}
      onCloseTab={handleCloseTab}
      onOpenKind={
        thread.isOwner !== false && thread.sandboxId
          ? handleOpenKind
          : undefined
      }
      menuKinds={
        thread.isOwner !== false && thread.sandboxId ? ["terminal"] : []
      }
      collapsed={collapsed}
      onCollapsedChange={setCollapsed}
      seamlessHeader={topTab === "git"}
    >
      {({ fullScreen }) => (
        <>
          {topTab === "git" ? (
            <>
              {reviewHeader}
              {tab === "diff" ? (
                <DiffFilesView
                  files={files}
                  revealFilePath={revealFilePath}
                  fullScreen={fullScreen}
                  hideHeader
                  emptyLabel={
                    turnDiff.isLoading
                      ? "Loading thread diff…"
                      : "No diff available."
                  }
                  truncated={truncated}
                />
              ) : (
                <div className="flex min-h-0 flex-1">
                  {tab === "review" ? (
                    <ReviewTab thread={thread} />
                  ) : (
                    <div className="min-h-0 flex-1 overflow-y-auto p-6 text-center text-xs text-muted-foreground/70">
                      Coming Soon
                    </div>
                  )}
                </div>
              )}
            </>
          ) : null}
          {panel.tabs.map((terminalTab) => (
            <div
              key={terminalTab.id}
              className={cn(
                "min-h-0 flex-1",
                terminalTab.id !== topTab && "hidden"
              )}
            >
              <TerminalPanel
                target={{ kind: "cloud", threadId: thread.id }}
                cwd=""
                groupId={terminalTab.id}
                terminals={terminals}
              />
            </div>
          ))}
        </>
      )}
    </AgentPanelShell>
  )
}
