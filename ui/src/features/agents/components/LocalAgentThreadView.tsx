import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { CircleAlert, FolderOpen, X } from "lucide-react"
import { Link } from "@tanstack/react-router"

import type { ImageChunk } from "@/features/agents/lib/types"
import type { PanelTabKind } from "@/features/agents/lib/panelTabs"
import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"
import { Alert, AlertDescription } from "@/components/ui/alert"
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
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import {
  useDesktopLocalThread,
  useLocalThreadDiff,
  useRefreshLocalThreads,
} from "@/features/agents/lib/desktopLocal"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

const LOCAL_PANEL_KINDS: ReadonlyArray<PanelTabKind> = [
  "review",
  "terminal",
  "browser",
  "files",
]

function promptContent(text: string, images: Array<ImageChunk>) {
  const trimmed = text.trim()
  const imageBlocks = images.map((image) => ({
    type: "image",
    base64: image.base64,
    mime_type: image.mimeType,
    ...(image.fileName ? { file_name: image.fileName } : {}),
  }))
  return [...imageBlocks, ...(trimmed ? [{ type: "text", text: trimmed }] : [])]
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function LocalAgentThreadView({ sessionId }: { sessionId: string }) {
  const stream = useAgentThreadStream()
  const threadQuery = useDesktopLocalThread(sessionId)
  const thread = threadQuery.data
  const refreshThreads = useRefreshLocalThreads()
  const initialPromptRef = useRef<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isMobile = useIsMobile()
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(sessionId)
  )
  const panel = usePanelTabs(sessionId)
  const terminals = useTerminalGroups(sessionId, thread?.cwd ?? "")
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

  const isRunning = stream.isLoading || thread?.status === "running"
  const diff = useLocalThreadDiff(
    sessionId,
    !panelCollapsed && panel.activeTab?.kind === "review" && Boolean(thread),
    isRunning
  )
  const files = useMemo(
    () => toPanelFiles(diff.data?.files ?? []),
    [diff.data?.files]
  )
  const messages = useMemo(
    () =>
      streamMessagesToUi(
        stream.messages,
        stream.toolCalls,
        stream.subagents,
        messageArrivalTimestamp
      ),
    [stream.messages, stream.toolCalls, stream.subagents]
  )

  const updateStatus = useCallback(
    async (status: "idle" | "running" | "error") => {
      await window.openSweDesktop?.updateLocalThread({
        threadId: sessionId,
        status,
      })
      refreshThreads(sessionId)
    },
    [refreshThreads, sessionId]
  )

  const submit = useCallback(
    async (prompt: string, images: Array<ImageChunk>) => {
      if (!thread) return false
      setError(null)
      await updateStatus("running")
      try {
        await stream.submit(
          {
            messages: [
              { type: "human", content: promptContent(prompt, images) },
            ],
          },
          {
            config: {
              configurable: {
                local_project_path: thread.cwd,
                ...(thread.modelId ? { agent_model_id: thread.modelId } : {}),
                ...(thread.effort ? { agent_effort: thread.effort } : {}),
              },
            },
          }
        )
        await updateStatus("idle")
        return true
      } catch (cause) {
        setError(errorMessage(cause))
        await updateStatus("error")
        return false
      }
    },
    [stream, thread, updateStatus]
  )

  useEffect(() => {
    if (!thread || initialPromptRef.current === sessionId) return
    initialPromptRef.current = sessionId
    void stream.hydrationPromise
      .then(() => window.openSweDesktop?.getLocalPrompt(sessionId))
      .then(async (pending) => {
        if (!pending) return
        if (await submit(pending.prompt, pending.images)) {
          await window.openSweDesktop?.clearLocalPrompt(sessionId)
        } else {
          initialPromptRef.current = null
        }
      })
      .catch((cause) => {
        initialPromptRef.current = null
        setError(errorMessage(cause))
        void updateStatus("error")
      })
  }, [sessionId, stream.hydrationPromise, submit, thread, updateStatus])

  useEffect(() => {
    if (!stream.error) return
    setError(errorMessage(stream.error))
    void updateStatus("error")
  }, [stream.error, updateStatus])

  if (!thread) {
    return (
      <div className="flex min-w-0 flex-1 flex-col items-center justify-center gap-3 text-xs text-muted-foreground">
        {threadQuery.isPending
          ? "Loading local Open SWE session…"
          : threadQuery.error
            ? errorMessage(threadQuery.error)
            : "This local session no longer exists."}
        {!threadQuery.isPending && (
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
            "mx-auto flex w-full max-w-3xl items-center gap-2 px-4 pt-3 text-xs text-muted-foreground",
            panelCollapsed && "pr-14"
          )}
        >
          <FolderOpen className="size-3.5" />
          <span className="truncate" title={thread.cwd}>
            {thread.cwd}
          </span>
          <span className="ml-auto shrink-0">This Mac</span>
        </div>
        {(error || thread.status === "error") && (
          <div className="mx-auto w-full max-w-3xl px-4 pt-3">
            <Alert variant="error">
              <CircleAlert />
              <AlertDescription>
                {error || "The local Open SWE agent stopped unexpectedly."}
              </AlertDescription>
            </Alert>
          </div>
        )}
        <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
          <Messages
            contentWidthClass="max-w-3xl"
            isStreaming={isRunning}
            isThinking={stream.isLoading}
            messages={messages}
            onOpenFile={handleOpenFile}
            streamIsLoading={stream.isLoading}
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
                activeRun={{ threadId: thread.id, running: isRunning }}
                busy={isRunning}
                compact
                onStop={async () => {
                  try {
                    await stream.stop()
                    await updateStatus("idle")
                  } catch (cause) {
                    setError(errorMessage(cause))
                    await updateStatus("error")
                  }
                }}
                onSubmit={async (prompt, images) => {
                  const terminalContext = terminalContexts.join("\n\n")
                  setTerminalContexts([])
                  await submit(
                    terminalContext
                      ? `${prompt}\n\nTerminal selection:\n\`\`\`\n${terminalContext}\n\`\`\``
                      : prompt,
                    images
                  )
                }}
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
                    localSessionId={thread.id}
                    cwd={thread.cwd}
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
