import { useCallback, useEffect, useMemo, useState } from "react"
import { CircleAlert, FolderOpen, X } from "lucide-react"
import { Link } from "@tanstack/react-router"

import type { PanelTabKind } from "@/features/agents/lib/panelTabs"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { useSidebarCollapsed } from "@/components/sidebar-layout"
import {
  AgentPanelShell,
  PANEL_MIN_CHAT_WIDTH,
  PanelComingSoon,
} from "@/features/agents/components/AgentPanelShell"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import {
  DiffFilesView,
  toPanelFiles,
} from "@/features/agents/components/DiffFilesView"
import { Messages } from "@/features/agents/components/messages"
import { TerminalPanel } from "@/features/agents/components/TerminalPanel"
import { usePanelTabs } from "@/features/agents/lib/panelTabs"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import {
  useDesktopAcpSession,
  useLocalSessionDiff,
} from "@/features/agents/lib/desktopAcp"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

const LOCAL_PANEL_KINDS: ReadonlyArray<PanelTabKind> = [
  "review",
  "terminal",
  "browser",
  "files",
]

export function LocalAgentThreadView({ sessionId }: { sessionId: string }) {
  const { session, messages, loaded } = useDesktopAcpSession(sessionId)
  const { models, defaultSelection } = useModelOptions()
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const sessionSelection = useMemo<ModelSelection | null>(() => {
    const modelId = session?.modelId
    const effort = session?.effort
    if (!modelId || !effort) return null
    return models.some(
      (model) => model.id === modelId && model.efforts.includes(effort)
    )
      ? { modelId, effort }
      : null
  }, [models, session?.effort, session?.modelId])
  const activeSelection = selection ?? sessionSelection ?? defaultSelection
  const isMobile = useIsMobile()
  const sidebarCollapsed = useSidebarCollapsed()
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(sessionId)
  )
  const panel = usePanelTabs(sessionId)
  const terminals = useTerminalGroups(sessionId, session?.cwd ?? "")
  const [revealFilePath, setRevealFilePath] = useState<string | null>(null)
  const [terminalContexts, setTerminalContexts] = useState<Array<string>>([])
  const handlePanelCollapsedChange = useCallback(
    (next: boolean) => {
      setPanelCollapsed(next)
      writeStoredPanelCollapsed(sessionId, next)
    },
    [sessionId]
  )
  const handleOpenFile = useCallback(
    (filePath: string) => {
      setRevealFilePath(filePath)
      panel.open({ id: "review", kind: "review" })
      handlePanelCollapsedChange(false)
    },
    [handlePanelCollapsedChange, panel]
  )
  const handleOpenKind = useCallback(
    (kind: PanelTabKind) => {
      if (kind !== "terminal") {
        panel.open({ id: kind, kind })
        return
      }
      panel.open({ id: terminals.addGroup(), kind })
    },
    [panel, terminals]
  )
  const handleSelectTab = useCallback(
    (id: string) => {
      panel.select(id)
      const group = terminals.state.terminalGroups.find(
        (candidate) => candidate.id === id
      )
      const terminalId = group?.terminalIds[0]
      if (terminalId) terminals.focus(terminalId)
    },
    [panel, terminals]
  )
  const handleCloseTab = useCallback(
    async (id: string) => {
      if (
        panel.tabs.find((tab) => tab.id === id)?.kind === "terminal" &&
        !(await terminals.closeGroup(id))
      ) {
        return
      }
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

  const isRunning =
    session?.status === "running" || session?.status === "starting"
  const diff = useLocalSessionDiff(
    sessionId,
    !panelCollapsed && panel.activeTab?.kind === "review" && Boolean(session),
    isRunning
  )
  const files = useMemo(
    () => toPanelFiles(diff.data?.files ?? []),
    [diff.data?.files]
  )

  if (!session) {
    return (
      <div className="flex min-w-0 flex-1 flex-col items-center justify-center gap-3 text-xs text-muted-foreground">
        {loaded
          ? "This local session is no longer running."
          : "Loading local Deep Agents Code session…"}
        {loaded && (
          <Link
            className="text-foreground underline underline-offset-4"
            to="/agents"
          >
            Start a new task
          </Link>
        )}
      </div>
    )
  }

  return (
    <div className="flex min-w-0 flex-1">
      <div
        className="flex min-w-0 flex-1 flex-col"
        style={isMobile ? undefined : { minWidth: PANEL_MIN_CHAT_WIDTH }}
      >
        <div
          className={cn(
            "flex w-full items-center gap-2 px-4 pt-3 text-xs text-muted-foreground",
            // Collapsed sidebars float controls in the top corners; keep the
            // path and target labels clear of them.
            sidebarCollapsed && "pl-32",
            panelCollapsed && "pr-14"
          )}
        >
          <FolderOpen className="size-3.5" />
          <span className="truncate" title={session.cwd}>
            {session.cwd}
          </span>
          <span className="ml-auto shrink-0">This Mac</span>
        </div>
        {session.status === "error" && (
          <div className="mx-auto w-full max-w-3xl px-4 pt-3">
            <Alert variant="error">
              <CircleAlert />
              <AlertDescription>
                Deep Agents Code stopped. Start a new local session to continue.
              </AlertDescription>
            </Alert>
          </div>
        )}
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
          <Messages
            contentWidthClass="max-w-3xl"
            isStreaming={isRunning}
            isThinking={isRunning}
            messages={messages}
            onOpenFile={handleOpenFile}
            streamIsLoading={isRunning}
          />
          <div className="shrink-0 px-4 pb-4">
            <div className="mx-auto w-full max-w-3xl min-w-0">
              {terminalContexts.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {terminalContexts.map((text, index) => (
                    <span
                      key={`${text.slice(0, 24)}:${index}`}
                      className="inline-flex max-w-full items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-[11px] text-muted-foreground"
                      title={text}
                    >
                      <span className="max-w-64 truncate">
                        Terminal selection
                      </span>
                      <button
                        type="button"
                        aria-label="Remove terminal selection"
                        onClick={() =>
                          setTerminalContexts((current) =>
                            current.filter(
                              (_, itemIndex) => itemIndex !== index
                            )
                          )
                        }
                      >
                        <X className="size-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <AgentPromptBar
                activeRun={{ threadId: session.id, running: isRunning }}
                busy={isRunning}
                compact
                disabled={session.status === "error"}
                onStop={() =>
                  window.openSweDesktop?.cancelAcpSession(session.id)
                }
                onSubmit={async (prompt, images) => {
                  const terminalContext = terminalContexts.join("\n\n")
                  setTerminalContexts([])
                  await window.openSweDesktop?.promptAcpSession({
                    sessionId: session.id,
                    prompt: terminalContext
                      ? `${prompt}\n\nTerminal selection:\n\`\`\`\n${terminalContext}\n\`\`\``
                      : prompt,
                    images,
                    modelId: activeSelection?.modelId,
                    effort: activeSelection?.effort,
                  })
                }}
                models={models}
                selection={activeSelection}
                onSelectionChange={setSelection}
                placeholder="Add a follow up"
              />
            </div>
          </div>
        </div>
      </div>
      <AgentPanelShell
        tabs={panel.tabs.map((tab) =>
          tab.kind === "terminal"
            ? { ...tab, title: terminalTabTitle(terminals, tab.id) }
            : tab
        )}
        activeTabId={panel.activeTabId}
        onSelectTab={handleSelectTab}
        onCloseTab={handleCloseTab}
        onOpenKind={handleOpenKind}
        menuKinds={LOCAL_PANEL_KINDS}
        collapsed={panelCollapsed}
        onCollapsedChange={handlePanelCollapsedChange}
      >
        {({ fullScreen }) => (
          <>
            {panel.activeTab?.kind === "review" && (
              <DiffFilesView
                files={files}
                revealFilePath={revealFilePath}
                fullScreen={fullScreen}
                emptyLabel={localDiffEmptyLabel(
                  diff.data?.status,
                  diff.isPending
                )}
                truncated={diff.data?.truncated}
              />
            )}
            {(panel.activeTab?.kind === "browser" ||
              panel.activeTab?.kind === "files") && <PanelComingSoon />}
            {/* Kept mounted across tabs: unmounting kills the user's shell. */}
            {panel.tabs
              .filter((tab) => tab.kind === "terminal")
              .map((tab) => (
                <div
                  key={tab.id}
                  className={cn(
                    "min-h-0 flex-1",
                    tab.id !== panel.activeTabId && "hidden"
                  )}
                >
                  <TerminalPanel
                    localSessionId={session.id}
                    cwd={session.cwd}
                    groupId={tab.id}
                    terminals={terminals}
                    onOpenFile={handleOpenFile}
                    onAddToChat={(text) =>
                      setTerminalContexts((current) => [...current, text])
                    }
                  />
                </div>
              ))}
          </>
        )}
      </AgentPanelShell>
    </div>
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

function localDiffEmptyLabel(
  status: string | undefined,
  isPending: boolean
): string {
  if (isPending) return "Reading changes…"
  if (status === "missing") return "This project is not a git repository."
  if (status === "error") return "Could not read this project's git changes."
  return "No changes yet."
}
