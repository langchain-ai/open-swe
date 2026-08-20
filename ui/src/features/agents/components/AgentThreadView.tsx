import { useCallback, useEffect, useMemo, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { CircleAlert as CircleAlertIcon, FolderOpen } from "lucide-react"

import type {
  AgentThread,
  ImageChunk,
  Message,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import type { AgentPanelTab } from "@/features/agents/components/AgentGitPanel"
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert"
import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { AgentGitPanel } from "@/features/agents/components/AgentGitPanel"
import { PANEL_MIN_CHAT_WIDTH } from "@/features/agents/components/AgentPanelShell"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { WorkflowApprovalCard } from "@/features/agents/components/WorkflowApprovalCard"
import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "@/features/agents/lib/gitPanelPreferences"
import { Messages } from "@/features/agents/components/messages"
import { latestContextTokens } from "@/features/agents/lib/contextUsage"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useSubmitAgentMessage } from "@/features/agents/lib/provider/useSubmitAgentMessage"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import { useAgentSkills } from "@/features/agents/lib/queries"
import { rejectPlan } from "@/lib/plan"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

interface AgentThreadViewProps {
  thread: AgentThread
  autoFocusComposer?: boolean
}

function messageText(message: Message): string {
  return message.chunks
    .map((chunk) => (chunk.kind === "text" ? chunk.text : ""))
    .join("\n")
    .trim()
}

/** Paths the agent has edited this thread, newest last, for `@file` mentions. */
function editedPaths(messages: Array<Message>): Array<string> {
  const paths = new Set<string>()
  for (const message of messages) {
    for (const chunk of message.chunks) {
      if (chunk.kind !== "tool-execution" || chunk.toolKind !== "edit") continue
      const path = chunk.input?.file_path ?? chunk.input?.path
      if (typeof path === "string" && path) paths.add(path)
    }
  }
  return [...paths]
}

function visibleQueuedMessages(
  queuedMessages: Array<QueuedThreadMessage> | undefined,
  messages: Array<Message>
): Array<QueuedThreadMessage> {
  const queued = queuedMessages ?? []
  if (queued.length === 0) return queued

  const userMessages = messages
    .filter((message) => message.author === "user")
    .map((message) => ({
      text: messageText(message),
      timestamp: Date.parse(message.timestamp),
      consumed: false,
    }))

  return queued.filter((queuedMessage) => {
    const queuedText = queuedMessage.content.trim()
    if (!queuedText) return true

    const match = userMessages.find((message) => {
      if (message.consumed || !message.text.includes(queuedText)) return false
      if (!Number.isFinite(message.timestamp)) return true
      return message.timestamp >= queuedMessage.createdAt - 1000
    })
    if (!match) return true

    match.consumed = true
    return false
  })
}

// The stream lives at the `/agents` layout (one persistent provider that
// survives the home → thread navigation), so this view only consumes it.
export function AgentThreadView({
  thread,
  autoFocusComposer = false,
}: AgentThreadViewProps) {
  const sendMessage = useSubmitAgentMessage(thread.id)
  const stream = useAgentThreadStream()
  const isMobile = useIsMobile()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const sidebarCollapsed = useSidebarCollapsed()
  const skills = useAgentSkills()

  const { models, defaultSelection } = useModelOptions()
  const threadSelection = useMemo<ModelSelection | null>(() => {
    if (!thread.model || !thread.effort) return null
    const supported = models.some(
      (m) => m.id === thread.model && m.efforts.includes(thread.effort ?? "")
    )
    if (!supported) return null
    return { modelId: thread.model, effort: thread.effort }
  }, [models, thread.model, thread.effort])
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const activeSelection = selection ?? threadSelection ?? defaultSelection
  const [planMode, setPlanMode] = useState<boolean | null>(null)
  const [planFeedbackPending, setPlanFeedbackPending] =
    useState(autoFocusComposer)
  const activePlanMode = planMode ?? thread.planMode ?? false
  const activeModel = models.find(
    (model) => model.id === activeSelection?.modelId
  )
  const submitMessage = useCallback(
    async (content: string, images: Array<ImageChunk>) => {
      if (planFeedbackPending) await rejectPlan(thread.id, false)
      await sendMessage.mutateAsync({
        content,
        images,
        model_id: activeSelection?.modelId ?? null,
        effort: activeSelection?.effort ?? null,
        plan_mode: activePlanMode,
      })
      setPlanFeedbackPending(false)
    },
    [
      activePlanMode,
      activeSelection?.effort,
      activeSelection?.modelId,
      planFeedbackPending,
      sendMessage,
      thread.id,
    ]
  )
  const usedTokens = useMemo(
    () => latestContextTokens(stream.messages),
    [stream.messages]
  )

  // Own the git panel's collapsed state so file links can reveal the panel.
  const [panelCollapsed, setPanelCollapsed] = useState(() =>
    readStoredPanelCollapsed(thread.id)
  )
  const [panelTab, setPanelTab] = useState<AgentPanelTab>("git")
  const handlePanelCollapsedChange = useCallback(
    (next: boolean) => {
      setPanelCollapsed(next)
      writeStoredPanelCollapsed(thread.id, next)
    },
    [thread.id]
  )
  const [revealFilePath, setRevealFilePath] = useState<string | null>(null)
  const handleOpenFile = useCallback(
    (filePath: string) => {
      setRevealFilePath(filePath)
      setPanelTab("git")
      handlePanelCollapsedChange(false)
    },
    [handlePanelCollapsedChange]
  )

  const baseMessages = useMemo<Array<Message>>(() => {
    const live = streamMessagesToUi(
      stream.messages,
      stream.toolCalls,
      messageArrivalTimestamp
    )
    if (live.length > 0) return live
    // Optimistic transcript seeded by `AgentsHome` on thread creation (the
    // only case where a fetched thread carries messages — `getThread` returns
    // none). Bridges the brief gap before the SDK's optimistic `submit` echo
    // lands in `stream.messages`.
    if (thread.messages.length > 0) return thread.messages
    return live
  }, [stream.messages, stream.toolCalls, thread.messages])

  const isStreaming = thread.status === "running" || stream.isLoading
  const activeRun = useMemo(
    () => ({ threadId: thread.id, running: thread.status === "running" }),
    [thread.id, thread.status]
  )
  const queuedMessages = useMemo(
    () => visibleQueuedMessages(thread.queuedMessages, baseMessages),
    [baseMessages, thread.queuedMessages]
  )
  const hasMessages = baseMessages.length > 0
  const hasConversation = hasMessages || queuedMessages.length > 0
  // The only file list the UI has: whatever the agent has already touched in
  // this thread. Those are also the paths a follow-up is most likely about.
  const mentionPaths = useMemo(() => editedPaths(baseMessages), [baseMessages])
  const isThinking = stream.isLoading
  const settingUpSandbox = isThinking && baseMessages.length === 0
  // The transcript hydrates from the SDK (`GET …/state` → `stream.messages`).
  // Show a loading state during that one-time fetch instead of the empty state.
  const isHydrating = stream.isThreadLoading && !hasMessages
  // A failed hydrate is indistinguishable from an empty thread in the snapshot,
  // so say so rather than claiming the thread has no messages. `stream.error`
  // also carries run failures, hence the dedicated hydration signal.
  const [hydrateRejected, setHydrateRejected] = useState(false)
  useEffect(() => {
    let active = true
    setHydrateRejected(false)
    stream.hydrationPromise.catch(() => {
      if (active) setHydrateRejected(true)
    })
    return () => {
      active = false
    }
  }, [stream.hydrationPromise])
  const hydrationFailed = !isHydrating && !hasMessages && hydrateRejected

  return (
    <div className="flex min-w-0 flex-1">
      <div
        className={cn(
          "flex min-w-0 flex-1 flex-col",
          thread.adminThread && "bg-destructive/4"
        )}
        style={isMobile ? undefined : { minWidth: PANEL_MIN_CHAT_WIDTH }}
      >
        <header className="relative z-10 h-11 shrink-0 border-b border-border/60 bg-background/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-background/60 after:to-transparent">
          <div
            className={cn(
              "flex h-full w-full items-center gap-3 px-4",
              sidebarCollapsed && (isDesktop ? "pl-32" : "pl-14"),
              panelCollapsed && "pr-14"
            )}
          >
            {thread.repoFullName && (
              <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-muted-foreground">
                <FolderOpen className="size-3.5 shrink-0" />
                <span className="truncate" title={thread.repoFullName}>
                  {thread.repoFullName}
                </span>
              </span>
            )}
            <span className="ml-auto shrink-0 text-xs text-muted-foreground">
              Cloud
            </span>
          </div>
        </header>
        {thread.status === "error" && (
          <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pt-3">
            <Alert variant="error" controlAlignment="first-line">
              <CircleAlertIcon />
              <AlertDescription>
                <span>
                  The last run hit an error before it could finish. Send another
                  message to retry.
                </span>
              </AlertDescription>
              {thread.traceUrl && (
                <AlertAction>
                  <a
                    href={thread.traceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-md px-2 py-1 text-xs font-medium text-destructive-foreground underline underline-offset-2 hover:bg-destructive/8"
                  >
                    Open trace
                  </a>
                </AlertAction>
              )}
            </Alert>
          </div>
        )}
        <WorkflowApprovalCard
          threadId={thread.id}
          pollWhileActive={isStreaming}
        />
        {hasConversation ? (
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            <Messages
              messages={baseMessages}
              threadId={thread.id}
              showPlanArtifact={
                thread.planStatus === "ready" || thread.planStatus === "shared"
              }
              onOpenFile={handleOpenFile}
              queuedMessages={queuedMessages}
              isStreaming={isStreaming}
              streamIsLoading={stream.isLoading}
              isThinking={isThinking}
              settingUpSandbox={settingUpSandbox}
              contentWidthClass="max-w-3xl"
            />
            <div className="shrink-0 px-4 pb-4">
              <div className="mx-auto w-full max-w-3xl min-w-0">
                <AgentPromptBar
                  placeholder="Add a follow up"
                  autoFocus={autoFocusComposer}
                  compact
                  busy={isStreaming}
                  activeRun={activeRun}
                  onSubmit={submitMessage}
                  models={models}
                  selection={activeSelection}
                  onSelectionChange={setSelection}
                  planMode={activePlanMode}
                  onPlanModeChange={setPlanMode}
                  mentionPaths={mentionPaths}
                  skills={skills.data}
                  contextUsage={{
                    usedTokens,
                    contextWindow: activeModel?.context_window ?? null,
                  }}
                />
              </div>
            </div>
          </div>
        ) : isHydrating ? (
          <div className="flex flex-1 items-center justify-center px-6">
            <img
              src="/logo-mark.png"
              alt="Loading conversation"
              className="size-12 animate-pulse"
            />
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
            {hydrationFailed ? (
              <Alert variant="error" className="max-w-3xl">
                <CircleAlertIcon />
                <AlertDescription>
                  <span>
                    This thread&apos;s messages could not be loaded. Reload to
                    try again.
                  </span>
                </AlertDescription>
              </Alert>
            ) : (
              <p className="text-xs text-muted-foreground/70">
                This thread has no messages yet.
              </p>
            )}
            <div className="w-full max-w-3xl">
              <AgentPromptBar
                placeholder="Send the first message"
                autoFocus={autoFocusComposer}
                compact
                busy={isStreaming}
                activeRun={activeRun}
                onSubmit={(content, images) =>
                  sendMessage.mutateAsync({
                    content,
                    images,
                    model_id: activeSelection?.modelId ?? null,
                    effort: activeSelection?.effort ?? null,
                  })
                }
                models={models}
                selection={activeSelection}
                onSelectionChange={setSelection}
                skills={skills.data}
                contextUsage={{
                  usedTokens,
                  contextWindow: activeModel?.context_window ?? null,
                }}
              />
            </div>
          </div>
        )}
      </div>
      <AgentGitPanel
        thread={thread}
        revealFilePath={revealFilePath}
        collapsed={panelCollapsed}
        requestedTab={panelTab}
        onCollapsedChange={handlePanelCollapsedChange}
        onTabChange={setPanelTab}
      />
    </div>
  )
}
