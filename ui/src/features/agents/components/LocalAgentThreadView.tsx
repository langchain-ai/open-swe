import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { CircleAlert, X } from "lucide-react"
import { Link } from "@tanstack/react-router"

import type {
  DesktopLocalPromptInput,
  DesktopLocalThreadSummary,
} from "@/desktop"
import type {
  ImageChunk,
  Message,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { AgentComposerDock } from "@/features/agents/components/composer/AgentComposerDock"
import { AgentThreadHeader } from "@/features/agents/components/AgentThreadHeader"
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
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { visibleQueuedMessages } from "@/features/agents/lib/queuedMessages"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useIsMobile } from "@/lib/useIsMobile"
import { useSession } from "@/lib/session"
import { useAgentThreadRuntime } from "@/features/agents/lib/AgentThreadStreamProvider"

function imageBlocks(images: Array<ImageChunk>) {
  return images.map((image) => ({
    type: "image",
    base64: image.base64,
    mime_type: image.mimeType,
    ...(image.fileName ? { file_name: image.fileName } : {}),
  }))
}

const QUEUE_KEY = "pending_messages"

type QueuedPayload = {
  text?: string
  images?: Array<{ base64?: string; mime_type?: string; file_name?: string }>
}

function payloadImages(payload: QueuedPayload): Array<ImageChunk> {
  return (payload.images ?? []).flatMap((block) =>
    block.base64 && block.mime_type
      ? [
          {
            kind: "image" as const,
            base64: block.base64,
            mimeType: block.mime_type,
            ...(block.file_name ? { fileName: block.file_name } : {}),
          },
        ]
      : []
  )
}

function promptContent(text: string, images: Array<ImageChunk>) {
  const trimmed = text.trim()
  return [
    ...imageBlocks(images),
    ...(trimmed ? [{ type: "text", text: trimmed }] : []),
  ]
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
  const login = session.data?.login
  const stream = useAgentThreadRuntime()
  const threadQuery = useDesktopLocalThread(sessionId)
  const thread = threadQuery.data
  const queryClient = useQueryClient()
  const skills = useAgentSkills({ enabled: Boolean(session.data) })
  const {
    models,
    defaultSelection,
    isLoading: modelsLoading,
  } = useModelOptions()
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
  const [queuedState, setQueuedState] = useState<{
    sessionId: string
    items: Array<QueuedThreadMessage>
  }>({ sessionId, items: [] })
  const queued = queuedState.sessionId === sessionId ? queuedState.items : []
  const queueNamespace = useMemo(() => ["queue", sessionId], [sessionId])
  const stoppedRef = useRef(false)
  const handoffRef = useRef(false)
  const isMobile = useIsMobile()
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
      stoppedRef.current = false
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

  // Mid-run follow-ups go to the thread's store queue, which
  // `check_message_queue_before_model` drains into the running agent, rather
  // than starting a second run on a busy thread.
  const enqueue = useCallback(
    async (prompt: string, images: Array<ImageChunk>) => {
      const text = prompt.trim()
      const existing = await stream.client.store.getItem(
        queueNamespace,
        QUEUE_KEY
      )
      const pending = existing?.value?.messages
      await stream.client.store.putItem(queueNamespace, QUEUE_KEY, {
        messages: [
          ...(Array.isArray(pending) ? pending : []),
          {
            content: {
              text,
              images: imageBlocks(images),
              ...(login && {
                sender: {
                  id: `github:${login}`,
                  platform: "github",
                  github_login: login,
                },
              }),
            },
          },
        ],
      })
      const createdAt = Date.now()
      setQueuedState((current) => ({
        sessionId,
        items: [
          ...(current.sessionId === sessionId ? current.items : []),
          { id: `queued-${createdAt}`, content: text, images, createdAt },
        ],
      }))
    },
    [login, queueNamespace, sessionId, stream.client]
  )

  // A live run does not guarantee another queue check: a follow-up written
  // after its last model call is never read. Once the run ends, take back
  // whatever the agent left behind and send it as a fresh run — unless the
  // user stopped the run, in which case the pending work is discarded.
  const flushUndrainedQueue = useCallback(async () => {
    const item = await stream.client.store.getItem(queueNamespace, QUEUE_KEY)
    if (!item) return
    await stream.client.store.deleteItem(queueNamespace, QUEUE_KEY)
    const pending = item.value?.messages
    if (stoppedRef.current || !Array.isArray(pending)) return
    const payloads = pending.map(
      (entry) =>
        ((entry as { content?: QueuedPayload }).content ?? {}) as QueuedPayload
    )
    const text = payloads
      .map((payload) => payload.text?.trim())
      .filter(Boolean)
      .join("\n\n")
    const images = payloads.flatMap(payloadImages)
    if (text || images.length > 0) await submit(text, images)
  }, [queueNamespace, stream.client, submit])

  useEffect(() => {
    if (isRunning || queued.length === 0 || handoffRef.current) return
    handoffRef.current = true
    // oxlint-disable-next-line react/set-state-in-effect
    setQueuedState({ sessionId, items: [] })
    void flushUndrainedQueue()
      .catch((cause) => setError(errorMessage(cause)))
      .finally(() => {
        handoffRef.current = false
      })
  }, [flushUndrainedQueue, isRunning, queued.length, sessionId])

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
        <AgentThreadHeader
          project={thread.cwd}
          target="This Mac"
          panelCollapsed={panelCollapsed}
        />
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
            queuedMessages={
              isRunning ? visibleQueuedMessages(queued, messages) : []
            }
            streamIsLoading={stream.isLoading}
          />
          <AgentComposerDock>
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
                          current.filter((_, itemIndex) => itemIndex !== index)
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
                  stoppedRef.current = true
                  await stream.stop()
                } catch (cause) {
                  setError(errorMessage(cause))
                }
              }}
              onSubmit={async (prompt, images) => {
                const terminalContext = terminalContexts.join("\n\n")
                setTerminalContexts([])
                const text = terminalContext
                  ? `${prompt}\n\nTerminal selection:\n\`\`\`\n${terminalContext}\n\`\`\``
                  : prompt
                if (!isRunning) {
                  await submit(text, images)
                  return
                }
                try {
                  await enqueue(text, images)
                } catch (cause) {
                  setError(errorMessage(cause))
                }
              }}
              placeholder="Add a follow up"
              skills={skills.data}
            />
          </AgentComposerDock>
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
