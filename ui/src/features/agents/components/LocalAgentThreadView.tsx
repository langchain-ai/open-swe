import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"
import { CircleAlert, FolderOpen, X } from "lucide-react"
import { Link } from "@tanstack/react-router"

import type {
  DesktopLocalPromptInput,
  DesktopLocalThreadSummary,
} from "@/desktop"
import type { ImageChunk } from "@/features/agents/lib/types"
import type { PanelTabKind } from "@/features/agents/lib/panelTabs"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { useSidebarCollapsed } from "@/components/sidebar-layout"
import {
  AgentPanelShell,
  PANEL_MIN_CHAT_WIDTH,
} from "@/features/agents/components/AgentPanelShell"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { ChangesPanel } from "@/features/agents/components/ChangesPanel"
import { toPanelFiles } from "@/features/agents/components/DiffFilesView"
import { Messages } from "@/features/agents/components/messages"
import { TerminalPanel } from "@/features/agents/components/TerminalPanel"
import { BrowserPanel } from "@/features/agents/components/BrowserPanel"
import {
  FileEditorPanel,
  FileExplorerPanel,
  type WorkspaceAdapter,
} from "@/features/agents/components/WorkspacePanel"
import {
  AGENT_COMMON_TABS,
  AGENT_PANEL_KINDS,
  CHANGES_TAB,
  BROWSER_TAB,
  FILES_TAB,
  PULL_REQUEST_TAB,
  usePanelTabs,
} from "@/features/agents/lib/panelTabs"
import { useAgentSkills } from "@/features/agents/lib/queries"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import {
  localThreadKeys,
  useDesktopLocalThread,
  useLocalThreadDiff,
} from "@/features/agents/lib/desktopLocal"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useRegisterAppCommands } from "@/lib/appCommands"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

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

function skillFiles(skills: DesktopLocalPromptInput["skills"]) {
  return Object.fromEntries(
    skills.map(({ name, description, instructions }) => [
      `/${name}/SKILL.md`,
      {
        content: `---\nname: ${JSON.stringify(name)}\ndescription: ${JSON.stringify(description)}\n---\n\n${instructions.trim()}\n`,
        encoding: "utf-8",
      },
    ])
  )
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function LocalAgentThreadView({ sessionId }: { sessionId: string }) {
  const stream = useAgentThreadStream()
  const threadQuery = useDesktopLocalThread(sessionId)
  const thread = threadQuery.data
  const queryClient = useQueryClient()
  const skills = useAgentSkills()
  const {
    models,
    defaultSelection,
    isLoading: modelsLoading,
  } = useModelOptions()
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  useEffect(() => setSelection(null), [sessionId])
  const threadSelection = useMemo<ModelSelection | null>(() => {
    if (!thread?.modelId || !thread.effort) return null
    return models.some(
      (model) =>
        model.id === thread.modelId &&
        model.efforts.includes(thread.effort ?? "")
    )
      ? { modelId: thread.modelId, effort: thread.effort }
      : null
  }, [models, thread?.effort, thread?.modelId])
  const activeSelection = selection ?? threadSelection ?? defaultSelection
  const initialPromptRef = useRef<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isMobile = useIsMobile()
  const sidebarCollapsed = useSidebarCollapsed()
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(sessionId)
  )
  const panel = usePanelTabs(sessionId, AGENT_COMMON_TABS)
  const terminals = useTerminalGroups(
    { kind: "local", sessionId },
    thread?.cwd ?? ""
  )
  const [revealFilePath, setRevealFilePath] = useState<string | null>(null)
  const [terminalContexts, setTerminalContexts] = useState<Array<string>>([])
  const [dirtyFiles, setDirtyFiles] = useState<ReadonlySet<string>>(new Set())
  const handleDirtyChange = useCallback((path: string, dirty: boolean) => {
    setDirtyFiles((current) => {
      const next = new Set(current)
      if (dirty) next.add(path)
      else next.delete(path)
      return next
    })
  }, [])
  const handlePanelCollapsedChange = useCallback(
    (next: boolean) => {
      setPanelCollapsed(next)
      writeStoredPanelCollapsed(sessionId, next)
    },
    [sessionId]
  )
  const handleOpenFile = useCallback(
    (filePath: string) => {
      const relativePath = filePath.startsWith(`${thread?.cwd}/`)
        ? filePath.slice((thread?.cwd.length ?? -1) + 1)
        : filePath
      setRevealFilePath(relativePath)
      panel.openFile(relativePath)
      handlePanelCollapsedChange(false)
    },
    [handlePanelCollapsedChange, panel, thread?.cwd]
  )
  const handleOpenKind = useCallback(
    (kind: PanelTabKind) => {
      if (kind === "terminal") panel.open({ id: terminals.addGroup(), kind })
      else if (kind === "changes") panel.open(CHANGES_TAB)
      else if (kind === "files") panel.open(FILES_TAB)
      else if (kind === "browser") panel.open(BROWSER_TAB)
      else if (kind === "pull-request") panel.open(PULL_REQUEST_TAB)
    },
    [panel, terminals]
  )
  const activePanelTabId = panel.activeTabId
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
      const tab = panel.tabs.find((candidate) => candidate.id === id)
      if (
        tab?.kind === "file" &&
        tab.resourceId &&
        dirtyFiles.has(tab.resourceId) &&
        !window.confirm("Discard unsaved changes?")
      )
        return
      if (tab?.kind === "terminal" && !(await terminals.closeGroup(id))) return
      panel.close(id)
    },
    [dirtyFiles, panel, terminals]
  )
  const toggleTerminal = useCallback(() => {
    if (!panelCollapsed && panel.activeTab?.kind === "terminal") {
      handlePanelCollapsedChange(true)
      return
    }
    handlePanelCollapsedChange(false)
    const existing = panel.tabs.find(
      (candidate) => candidate.kind === "terminal"
    )
    if (existing) handleSelectTab(existing.id)
    else handleOpenKind("terminal")
  }, [
    handleOpenKind,
    handlePanelCollapsedChange,
    handleSelectTab,
    panel.activeTab?.kind,
    panel.tabs,
    panelCollapsed,
  ])
  const threadCommands = useMemo(
    () =>
      thread
        ? [
            {
              id: "toggle-terminal",
              label: "Toggle terminal",
              aliases: ["open terminal", "hide terminal"],
              shortcuts: ["ctrl+`"],
              group: "Workspace",
              run: toggleTerminal,
            },
            {
              id: "toggle-work-panel",
              label: "Toggle work panel",
              aliases: ["show panel", "hide panel", "changes panel"],
              shortcuts: ["mod+alt+b"],
              group: "Workspace",
              run: () => handlePanelCollapsedChange(!panelCollapsed),
            },
          ]
        : [],
    [handlePanelCollapsedChange, panelCollapsed, thread, toggleTerminal]
  )
  useRegisterAppCommands(threadCommands)

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
    !panelCollapsed && activePanelTabId === "changes" && Boolean(thread),
    isRunning
  )
  const files = useMemo(
    () => toPanelFiles(diff.data?.files ?? []),
    [diff.data?.files]
  )
  const repository = diff.data?.repository
  const pr = repository?.pr
  const workspace = useMemo<WorkspaceAdapter>(
    () => ({
      key: `local:${sessionId}`,
      list: (path) => window.openSweDesktop!.listLocalFiles(sessionId, path),
      read: (path) => window.openSweDesktop!.readLocalFile(sessionId, path),
      write: (path, content) =>
        window.openSweDesktop!.writeLocalFile(sessionId, path, content),
    }),
    [sessionId]
  )
  const messages = useMemo(
    () =>
      streamMessagesToUi(
        stream.messages,
        stream.toolCalls,
        messageArrivalTimestamp
      ),
    [stream.messages, stream.toolCalls]
  )

  const updateStatus = useCallback(
    async (
      status: "idle" | "running" | "error",
      model?: ModelSelection | null
    ) => {
      const updated = await window.openSweDesktop?.updateLocalThread({
        threadId: sessionId,
        status,
        viewed: status === "running",
        ...(model && { modelId: model.modelId, effort: model.effort }),
      })
      if (!updated) return
      queryClient.setQueryData(localThreadKeys.detail(sessionId), updated)
      queryClient.setQueryData<Array<DesktopLocalThreadSummary>>(
        localThreadKeys.all,
        (threads = []) =>
          threads.map((thread) => (thread.id === sessionId ? updated : thread))
      )
    },
    [queryClient, sessionId]
  )

  useEffect(() => {
    if (!thread || thread.viewed || isRunning) return
    void window.openSweDesktop
      ?.updateLocalThread({
        threadId: sessionId,
        status: thread.status,
        viewed: true,
      })
      .then((updated) => {
        if (!updated) return
        queryClient.setQueryData(localThreadKeys.detail(sessionId), updated)
        queryClient.setQueryData<Array<DesktopLocalThreadSummary>>(
          localThreadKeys.all,
          (threads = []) =>
            threads.map((item) => (item.id === sessionId ? updated : item))
        )
      })
  }, [isRunning, queryClient, sessionId, thread])

  const submit = useCallback(
    async (
      prompt: string,
      images: Array<ImageChunk>,
      skills: DesktopLocalPromptInput["skills"] = []
    ) => {
      if (!thread) return false
      setError(null)
      const credential =
        await window.openSweDesktop?.localModelCredentialStatus(
          activeSelection?.modelId
        )
      if (credential && !credential.available) {
        setError(
          `Set ${credential.variable} in the environment before starting Open SWE.`
        )
        return false
      }
      try {
        await updateStatus("running", activeSelection)
        await stream.submit(
          {
            messages: [
              { type: "human", content: promptContent(prompt, images) },
            ],
            ...(skills.length ? { files: skillFiles(skills) } : {}),
          },
          {
            config: {
              configurable: {
                source: "desktop",
                local_project_path: thread.cwd,
                ...(activeSelection && {
                  agent_model_id: activeSelection.modelId,
                  agent_effort: activeSelection.effort,
                }),
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
    [activeSelection, stream, thread, updateStatus]
  )

  useEffect(() => {
    if (modelsLoading || !thread || initialPromptRef.current === sessionId)
      return
    initialPromptRef.current = sessionId
    void stream.hydrationPromise
      .then(() => window.openSweDesktop?.getLocalPrompt(sessionId))
      .then(async (pending) => {
        if (!pending) return
        if (await submit(pending.prompt, pending.images, pending.skills)) {
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
  }, [
    modelsLoading,
    sessionId,
    stream.hydrationPromise,
    submit,
    thread,
    updateStatus,
  ])

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
        <header className="relative z-10 h-11 shrink-0 border-b border-border/60 bg-background/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-background/60 after:to-transparent">
          <div
            className={cn(
              "flex h-full w-full items-center gap-3 px-4",
              sidebarCollapsed && "pl-32",
              panelCollapsed && "pr-14"
            )}
          >
            <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-muted-foreground">
              <FolderOpen className="size-3.5 shrink-0" />
              <span className="truncate" title={thread.cwd}>
                {thread.cwd}
              </span>
            </span>
            <span className="ml-auto shrink-0 text-xs text-muted-foreground">
              This Mac
            </span>
          </div>
        </header>
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
                models={models}
                selection={activeSelection}
                onSelectionChange={setSelection}
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
                skills={skills.data}
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
        activeTabId={activePanelTabId}
        onSelectTab={handleSelectTab}
        onCloseTab={handleCloseTab}
        onOpenKind={handleOpenKind}
        menuKinds={AGENT_PANEL_KINDS.filter(
          (kind) => kind !== "pull-request" || Boolean(pr)
        )}
        collapsed={panelCollapsed}
        onCollapsedChange={handlePanelCollapsedChange}
      >
        {({ fullScreen }) => (
          <>
            {activePanelTabId === "changes" && (
              <ChangesPanel
                files={files}
                status={diff.data?.status}
                isLoading={diff.isPending}
                isFetching={diff.isFetching}
                error={diff.error}
                truncated={diff.data?.truncated}
                branch={repository?.branch}
                pr={pr}
                revealFilePath={revealFilePath}
                fullScreen={fullScreen}
                onRefresh={() => void diff.refetch()}
              />
            )}
            {panel.activeTab?.kind === "files" && (
              <FileExplorerPanel adapter={workspace} onOpen={panel.openFile} />
            )}
            {panel.tabs
              .filter((tab) => tab.kind === "file" && tab.resourceId)
              .map((tab) => (
                <div
                  key={tab.id}
                  className={cn(
                    "flex min-h-0 flex-1 flex-col",
                    tab.id !== activePanelTabId && "hidden"
                  )}
                >
                  <FileEditorPanel
                    adapter={workspace}
                    path={tab.resourceId!}
                    onDirtyChange={handleDirtyChange}
                  />
                </div>
              ))}
            {panel.tabs
              .filter((tab) => tab.kind === "browser")
              .map((tab) => (
                <div
                  key={tab.id}
                  className={cn(
                    "flex min-h-0 flex-1 flex-col",
                    tab.id !== activePanelTabId && "hidden"
                  )}
                >
                  <BrowserPanel
                    openExternal={(url) =>
                      void window.openSweDesktop?.openExternal(url)
                    }
                  />
                </div>
              ))}
            {panel.activeTab?.kind === "pull-request" && (
              <ChangesPanel
                files={files}
                status={diff.data?.status}
                isLoading={diff.isPending}
                isFetching={diff.isFetching}
                error={diff.error}
                truncated={diff.data?.truncated}
                branch={repository?.branch}
                pr={pr}
                fullScreen={fullScreen}
                onRefresh={() => void diff.refetch()}
              />
            )}
            {/* Kept mounted across tabs: unmounting kills the user's shell. */}
            {panel.tabs
              .filter((tab) => tab.kind === "terminal")
              .map((tab) => (
                <div
                  key={tab.id}
                  className={cn(
                    "min-h-0 flex-1",
                    tab.id !== activePanelTabId && "hidden"
                  )}
                >
                  <TerminalPanel
                    target={{ kind: "local", sessionId: thread.id }}
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
