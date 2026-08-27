import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"
import { CircleAlert, CloudUpload, FolderOpen, X } from "lucide-react"
import { Link, useNavigate } from "@tanstack/react-router"

import type {
  DesktopLocalPromptInput,
  DesktopLocalThreadSummary,
} from "@/desktop"
import type { ImageChunk, Message } from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { Button } from "@/components/ui/button"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { ChangesPanel } from "@/features/agents/components/ChangesPanel"
import { toPanelFiles } from "@/features/agents/components/DiffFilesView"
import { Messages } from "@/features/agents/components/messages"
import { AgentRightPanel } from "@/features/agents/components/panel/AgentRightPanel"
import { SIBLING_COLUMN_MIN_WIDTH } from "@/features/agents/components/panel/RightPanelShell"
import {
  selectThreadDiffScope,
  useDiffPanelStore,
} from "@/features/agents/lib/diffPanelStore"
import {
  selectThreadRightPanelState,
  useRightPanelStore,
} from "@/features/agents/lib/rightPanelStore"
import { useAgentSkills } from "@/features/agents/lib/queries"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import {
  ensureDesktopModelCredential,
  localThreadKeys,
  useDesktopLocalThread,
  useLocalThreadActivity,
  useLocalThreadDiff,
  useLocalThreadPrDiff,
} from "@/features/agents/lib/desktopLocal"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { agentsApi } from "@/features/agents/lib/api"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"
import { useSession } from "@/lib/session"

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
  const session = useSession()
  const navigate = useNavigate()
  const stream = useAgentThreadStream()
  const threadQuery = useDesktopLocalThread(sessionId)
  const thread = threadQuery.data
  const queryClient = useQueryClient()
  const skills = useAgentSkills({ enabled: Boolean(session.data) })
  const {
    models,
    defaultSelection,
    isLoading: modelsLoading,
  } = useModelOptions()
  const [transferring, setTransferring] = useState(false)
  const [sessionSelection, setSessionSelection] = useState<{
    sessionId: string
    selection: ModelSelection | null
  }>({ sessionId, selection: null })
  const selection =
    sessionSelection.sessionId === sessionId ? sessionSelection.selection : null
  const setSelection = (next: ModelSelection | null) =>
    setSessionSelection({ sessionId, selection: next })
  const threadModelId = thread?.modelId
  const threadEffort = thread?.effort
  const threadSelection = useMemo<ModelSelection | null>(() => {
    if (!threadModelId || !threadEffort) return null
    return models.some(
      (model) =>
        model.id === threadModelId && model.efforts.includes(threadEffort)
    )
      ? { modelId: threadModelId, effort: threadEffort }
      : null
  }, [models, threadEffort, threadModelId])
  const activeSelection = selection ?? threadSelection ?? defaultSelection
  const initialPromptRef = useRef<string | null>(null)
  const acknowledgedRef = useRef<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isMobile = useIsMobile()
  const sidebarCollapsed = useSidebarCollapsed()
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(sessionId)
  )
  const threadRef = useMemo(
    () => ({ scope: "local" as const, threadId: sessionId }),
    [sessionId]
  )
  const openSurface = useRightPanelStore((state) => state.open)
  const activeSurfaceId = useRightPanelStore(
    (state) =>
      selectThreadRightPanelState(state.byThreadKey, threadRef).activeSurfaceId
  )
  const terminals = useTerminalGroups(
    { kind: "local", sessionId },
    thread?.cwd ?? ""
  )
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
      openSurface(threadRef, "diff")
      handlePanelCollapsedChange(false)
    },
    [handlePanelCollapsedChange, openSurface, threadRef]
  )

  const activity = useLocalThreadActivity()[sessionId]
  const isRunning =
    stream.isLoading ||
    (Boolean(thread?.pending) && !error) ||
    activity === "running"
  const diffVisible =
    !panelCollapsed && activeSurfaceId === "diff" && Boolean(thread)
  const selectScope = useDiffPanelStore((state) => state.selectScope)
  // Also the source of the branch/PR metadata, so it stays enabled in either
  // scope: it is what tells us the branch has a pull request at all.
  const checkpointDiff = useLocalThreadDiff(sessionId, diffVisible, isRunning)
  // The pull request is what tells us the base to diff the branch against.
  const branchScopeAvailable = Boolean(checkpointDiff.data?.repository?.pr)
  const scope = useDiffPanelStore((state) =>
    selectThreadDiffScope(state.byThreadKey, threadRef, branchScopeAvailable)
  )
  const branchDiff = useLocalThreadPrDiff(
    sessionId,
    diffVisible && scope === "branch",
    isRunning
  )
  const repository =
    branchDiff.data?.repository ?? checkpointDiff.data?.repository
  const pr = repository?.pr ?? null
  const diff = scope === "branch" ? branchDiff : checkpointDiff
  const files = useMemo(
    () => toPanelFiles(diff.data?.files ?? []),
    [diff.data?.files]
  )
  const transferToCloud = useCallback(async () => {
    if (!thread || !window.openSweDesktop) return
    if (
      !window.confirm(
        "Continue this conversation in cloud? Local files, uncommitted changes, and terminal history will not be transferred."
      )
    )
      return
    setTransferring(true)
    setError(null)
    try {
      const exported = await window.openSweDesktop.exportLocalThread(sessionId)
      const cloudThread = await agentsApi.importLocalThread({
        ...exported,
        repo: pr?.repoFullName ?? null,
      })
      await navigate({
        to: "/agents/$threadId",
        params: { threadId: cloudThread.id },
      })
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setTransferring(false)
    }
  }, [navigate, pr, sessionId, thread])

  const messages = useMemo(() => {
    const live = streamMessagesToUi(
      stream.messages,
      stream.toolCalls,
      messageArrivalTimestamp
    )
    if (live.length > 0 || !thread?.pending) return live
    const text = thread.pending.prompt.trim()
    return [
      {
        id: `optimistic-user-${sessionId}`,
        author: "user",
        timestamp: new Date(thread.createdAt).toISOString(),
        chunks: [
          ...thread.pending.images,
          ...(text ? [{ kind: "text" as const, text }] : []),
        ],
      } satisfies Message,
    ]
  }, [sessionId, stream.messages, stream.toolCalls, thread])

  const rememberSelection = useCallback(
    async (model?: ModelSelection | null) => {
      if (!model) return
      const updated = await window.openSweDesktop?.updateLocalThread({
        threadId: sessionId,
        viewed: true,
        modelId: model.modelId,
        effort: model.effort,
      })
      if (!updated) return
      queryClient.setQueryData(localThreadKeys.detail(sessionId), updated)
      queryClient.setQueryData<Array<DesktopLocalThreadSummary>>(
        localThreadKeys.all,
        (threads = []) =>
          threads.map((entry) => (entry.id === sessionId ? updated : entry))
      )
    },
    [queryClient, sessionId]
  )

  useEffect(() => {
    if (isRunning) {
      acknowledgedRef.current = null
      return
    }
    if (!thread || acknowledgedRef.current === sessionId) return
    acknowledgedRef.current = sessionId
    void window.openSweDesktop
      ?.updateLocalThread({ threadId: sessionId, viewed: true })
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
      promptSkills: DesktopLocalPromptInput["skills"] = []
    ) => {
      if (!thread) return false
      setError(null)
      const credentialError = await ensureDesktopModelCredential(
        activeSelection?.modelId
      )
      if (credentialError) {
        setError(credentialError)
        return false
      }
      try {
        await rememberSelection(activeSelection)
        await stream.submit(
          {
            messages: [
              { type: "human", content: promptContent(prompt, images) },
            ],
            ...(promptSkills.length ? { files: skillFiles(promptSkills) } : {}),
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
        return true
      } catch (cause) {
        setError(errorMessage(cause))
        return false
      }
    },
    [activeSelection, rememberSelection, stream, thread]
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
          const updated =
            await window.openSweDesktop?.clearLocalPrompt(sessionId)
          if (updated)
            queryClient.setQueryData(localThreadKeys.detail(sessionId), updated)
        } else {
          initialPromptRef.current = null
        }
      })
      .catch((cause) => {
        initialPromptRef.current = null
        setError(errorMessage(cause))
      })
  }, [
    modelsLoading,
    queryClient,
    sessionId,
    stream.hydrationPromise,
    submit,
    thread,
  ])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    if (stream.error) setError(errorMessage(stream.error))
  }, [stream.error])

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
        style={isMobile ? undefined : { minWidth: SIBLING_COLUMN_MIN_WIDTH }}
      >
        <header
          data-desktop-drag-region=""
          className="relative z-10 h-11 shrink-0 border-b border-border/60 bg-background/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-background/60 after:to-transparent"
        >
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
            {session.data && (
              <Button
                size="sm"
                variant="outline"
                disabled={isRunning || transferring}
                onClick={() => void transferToCloud()}
              >
                <CloudUpload />
                {transferring ? "Transferring…" : "Continue in cloud"}
              </Button>
            )}
            <span className="shrink-0 text-xs text-muted-foreground">
              This Mac
            </span>
          </div>
        </header>
        {(error || activity === "error") && (
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
            isThinking={isRunning}
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
                disabled={transferring}
                compact
                models={models}
                selection={activeSelection}
                onSelectionChange={setSelection}
                onStop={async () => {
                  try {
                    await stream.stop()
                  } catch (cause) {
                    setError(errorMessage(cause))
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
      <AgentRightPanel
        threadRef={threadRef}
        terminals={terminals}
        terminalTarget={{ kind: "local", sessionId: thread.id }}
        cwd={thread.cwd}
        terminalAvailable
        diffAvailable
        collapsed={panelCollapsed}
        onCollapsedChange={handlePanelCollapsedChange}
        onTerminalOpenFile={handleOpenFile}
        onTerminalAddToChat={(text) =>
          setTerminalContexts((current) => [...current, text])
        }
        renderDiff={({ fullScreen }) => (
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
            scope={scope}
            branchScopeAvailable={branchScopeAvailable}
            onScopeChange={(next) => selectScope(threadRef, next)}
          />
        )}
      />
    </div>
  )
}
